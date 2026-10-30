import json

from soundscape.remote import PAGE, RemoteApp, make_handler
from tests.conftest import MAC_A, MAC_B


def test_page_and_status(session, world):
    app = RemoteApp(session, log=lambda _m: None)
    status, body, ctype = app.handle("GET", "/", None)
    assert status == 200 and body == PAGE and "html" in ctype
    status, body, _ = app.handle("GET", "/api/status?x=1", None)
    assert status == 200 and body["up"] is False
    assert app.handle("GET", "/nope", None)[0] == 404
    assert app.handle("DELETE", "/api/status", None)[0] == 404


def test_controls(session, world, runner):
    app = RemoteApp(session, log=lambda _m: None)
    assert app.handle("POST", "/api/up", {})[1]["ok"]
    assert world.default_sink == "soundscape"
    status, body, _ = app.handle("POST", "/api/volume", {"target": "all", "percent": 33})
    assert status == 200 and world.sinks["soundscape"]["volume"] == 33
    app.handle("POST", "/api/volume", {"target": MAC_A, "percent": 70})
    assert world.sinks[world.bt_sink_name(MAC_A)]["volume"] == 70
    status, body, _ = app.handle("POST", "/api/offset", {"target": "right", "delta_ms": 4})
    assert body["offset_ms"] == 4 and world.delays["soundscape_right_delay"] == 0.004
    status, body, _ = app.handle("POST", "/api/offset", {"target": "right", "offset_ms": 2.5})
    assert body["offset_ms"] == 2.5
    status, body, _ = app.handle("POST", "/api/mute", {"target": "left", "toggle": True})
    assert body["muted"] is True and world.sinks[world.bt_sink_name(MAC_A)]["mute"]
    status, body, _ = app.handle("POST", "/api/mute", {"target": "left", "toggle": True})
    assert body["muted"] is False
    status, body, _ = app.handle("POST", "/api/mute", {"target": "all", "mute": True})
    assert world.sinks["soundscape"]["mute"]
    status, body, _ = app.handle("POST", "/api/mute", {"target": "all", "toggle": True})
    assert body["muted"] is False
    status, body, _ = app.handle("POST", "/api/media", {"action": "next"})
    assert body["message"] == "install playerctl for media keys"
    runner.tools.add("playerctl")
    status, body, _ = app.handle("POST", "/api/media", {"action": "play-pause"})
    assert body["ok"]
    assert app.handle("POST", "/api/media", {"action": "explode"})[0] == 400
    assert app.handle("POST", "/api/test", {})[1]["ok"]
    assert app.handle("POST", "/api/down", {})[1]["ok"]
    assert world.default_sink.startswith("alsa_output")


def test_bad_requests(session, world):
    app = RemoteApp(session, log=lambda _m: None)
    assert app.handle("POST", "/api/volume", {"target": "all"})[0] == 400
    status, body, _ = app.handle("POST", "/api/volume", {"target": "ghost", "percent": 5})
    assert status == 400 and "hint" in body
    assert app.handle("POST", "/api/unknown", {})[0] == 400
    assert app.handle("POST", "/api/offset", {"target": "right", "delta_ms": "x"})[0] == 400


def test_mute_toggle_for_unknown_target_uses_memory(session, world):
    app = RemoteApp(session, log=lambda _m: None)
    assert app._current_mute("ghost") is False
    app._mute_state["ghost"] = True
    assert app._current_mute("ghost") is True
    assert app._current_mute(MAC_B) is False


def test_http_handler_roundtrip(session, world):
    import io
    app = RemoteApp(session, log=lambda _m: None)
    Handler = make_handler(app)

    class FakeRequest:
        def __init__(self, raw):
            self._r = io.BytesIO(raw)
            self.out = io.BytesIO()

        def makefile(self, mode, *a, **k):
            return self._r if "r" in mode else self.out

        def sendall(self, data):
            self.out.write(data)

        def close(self):
            pass

    body = json.dumps({"target": "all", "percent": 12}).encode()
    req = FakeRequest(b"POST /api/volume HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
    session.up()
    Handler(req, ("127.0.0.1", 1), None)
    assert b"200" in req.out.getvalue().split(b"\r\n")[0]
    assert world.sinks["soundscape"]["volume"] == 12
    bad = FakeRequest(b"POST /api/volume HTTP/1.1\r\nHost: x\r\nContent-Length: 4\r\n\r\n{bad")
    Handler(bad, ("127.0.0.1", 1), None)
    assert b"400" in bad.out.getvalue().split(b"\r\n")[0]
    get = FakeRequest(b"GET /api/status HTTP/1.1\r\nHost: x\r\n\r\n")
    Handler(get, ("127.0.0.1", 1), None)
    assert b'"up": true' in get.out.getvalue()


def test_token_required_when_configured(session, world):
    app = RemoteApp(session, log=lambda _m: None, token="s3cret")
    assert app.handle("GET", "/api/status", None)[0] == 401
    assert app.handle("GET", "/api/status?token=wrong", None)[0] == 401
    assert app.handle("GET", "/api/status?token=s3cret", None)[0] == 200
    assert app.handle("GET", "/api/status", None, {"X-Soundscape-Token": "s3cret"})[0] == 200
    assert app.handle("POST", "/api/volume", {"target": "all", "percent": 5}, {"X-Soundscape-Token": "nope"})[0] == 401
    assert RemoteApp(session, log=lambda _m: None, token="").authorized("/x", None)


def test_real_server_handles_concurrent_requests(session, world):
    import json as _json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    session.up()
    app = RemoteApp(session, log=lambda _m: None, token="t")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    results = []

    def hit(i):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/volume", method="POST",
                                     data=_json.dumps({"target": "all", "percent": 10 + i}).encode(),
                                     headers={"content-type": "application/json", "X-Soundscape-Token": "t"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            results.append(resp.status)

    workers = [threading.Thread(target=hit, args=(i,)) for i in range(12)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=10)
    try:
        assert results == [200] * 12
        assert 10 <= world.sinks["soundscape"]["volume"] <= 21
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/?token=t", timeout=5) as resp:
            assert b"X-Soundscape-Token" in resp.read()
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5)
            assert False, "token missing should be rejected"
        except urllib.error.HTTPError as e:
            assert e.code == 401
    finally:
        httpd.shutdown()
        httpd.server_close()

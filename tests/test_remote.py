import json
import struct
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from soundscape import tones
from soundscape.remote import PAGE, Jobs, RemoteApp, make_handler
from soundscape.sync import RECORD_RATE
from tests.conftest import MAC_A, MAC_B, FakeProcess


def app_for(session, **kw):
    return RemoteApp(session, log=lambda _m: None, synchronous_jobs=True, **kw)


def done(resp):
    """Unwrap a synchronous job response and assert it succeeded."""
    status, body, _ = resp
    assert status == 200, body
    job = body["job"]
    assert job["state"] == "done", job
    return job["result"]


def test_page_and_status(session, world):
    app = app_for(session)
    status, body, ctype = app.handle("GET", "/", None)
    assert status == 200 and "Signal path" in body and "html" in ctype and body == PAGE
    status, body, _ = app.handle("GET", "/api/status?x=1", None)
    assert status == 200 and body["up"] is False
    assert body["config"]["engine"] == "auto" and body["version"] and body["engines"] == ["auto", "pipewire", "pulse"]
    assert body["job"] is None and body["last_sync"] is None
    assert app.handle("GET", "/nope", None)[0] == 404
    assert app.handle("DELETE", "/api/status", None)[0] == 404
    assert app.handle("GET", "/api/job?id=zzz", None)[0] == 404


def test_lifecycle_jobs_and_quick_controls(session, world, runner):
    app = app_for(session)
    assert done(app.handle("POST", "/api/up", {})) == "pipewire"
    assert world.default_sink == "soundscape"
    assert app.handle("GET", "/api/status", None)[1]["up"]
    status, body, _ = app.handle("POST", "/api/volume", {"target": "all", "percent": 33})
    assert status == 200 and world.sinks["soundscape"]["volume"] == 33
    app.handle("POST", "/api/volume", {"target": MAC_A, "percent": 70})
    assert world.sinks[world.bt_sink_name(MAC_A)]["volume"] == 70
    status, body, _ = app.handle("POST", "/api/offset", {"target": "right", "delta_ms": 4})
    assert body["offset_ms"] == 4 and body["offsets"] == {"left": 0.0, "right": 4.0}
    assert world.delays["soundscape_right_delay"] == 0.004
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
    assert app.handle("POST", "/api/media", {"action": "play-pause"})[1]["ok"]
    assert app.handle("POST", "/api/media", {"action": "explode"})[0] == 400
    assert done(app.handle("POST", "/api/test", {})) is True
    assert done(app.handle("POST", "/api/test", {"target": "left"})) is True
    assert done(app.handle("POST", "/api/down", {})) is True
    assert world.default_sink.startswith("alsa_output")
    log = app.handle("GET", "/api/log?n=5", None)[1]
    assert log and all("t" in e and "msg" in e for e in log)


def test_scan_add_remove_config_doctor_sources(session, world):
    app = app_for(session)
    world.add_device("AA:BB:CC:DD:EE:03", "Bedroom", rssi=-40)
    found = done(app.handle("POST", "/api/scan", {"seconds": 1}))
    assert any(d["address"] == "AA:BB:CC:DD:EE:03" and d["is_audio"] for d in found)
    added = done(app.handle("POST", "/api/add", {"address": "AA:BB:CC:DD:EE:03", "position": "Bed Room"}))
    assert added["position"] == "bed_room" and world.devices["AA:BB:CC:DD:EE:03"]["connected"]
    devices = app.handle("GET", "/api/devices", None)[1]
    assert any(d["address"] == "AA:BB:CC:DD:EE:03" and d["connected"] for d in devices)
    status, body, _ = app.handle("POST", "/api/remove", {"target": "bed_room", "forget": True})
    assert body["removed"] == "Bedroom" and "AA:BB:CC:DD:EE:03" not in world.devices
    status, body, _ = app.handle("POST", "/api/config", {"key": "stereo_split", "value": True})
    assert status == 200 and session.config.stereo_split is True and body["note"] is None
    assert app.handle("POST", "/api/config", {"key": "engine", "value": "jack"})[0] == 400
    assert app.handle("POST", "/api/config", {"key": "speakers", "value": []})[0] == 400
    checks = app.handle("GET", "/api/doctor", None)[1]
    assert checks and {c["status"] for c in checks} <= {"ok", "warn", "fail"}
    sources = app.handle("GET", "/api/sources", None)[1]
    assert sources["recommended"] == world.default_source and any(x["speaker_mic"] for x in sources["sources"])
    # a job that fails reports the error with the hint, not a crash
    status, body, _ = app.handle("POST", "/api/add", {"address": "not-a-mac"})
    assert body["job"]["state"] == "error" and "Bluetooth address" in body["job"]["error"]


def synthetic_recording(latency_a, latency_b, lead=0.6, seconds=8.0):
    template = tones.chirp(RECORD_RATE)
    rec = [0.0] * int(seconds * RECORD_RATE)
    for at, gain in ((lead + tones.CALIBRATION_FIRST_S + latency_a, 0.5), (lead + tones.CALIBRATION_SECOND_S + latency_b, 0.45)):
        start = int(at * RECORD_RATE)
        for i, v in enumerate(template):
            rec[start + i] += gain * v
    return struct.pack(f"<{len(rec)}h", *[int(max(-1, min(1, v)) * 32767) for v in rec])


def test_sync_job_reports_measurement(session, world, runner):
    def factory(argv, kwargs):
        kwargs["stdout"].write(synthetic_recording(0.180, 0.192))
        proc = FakeProcess(argv, kwargs)
        proc.returncode = 0
        return proc
    runner.on_popen("parec", factory)
    app = app_for(session)
    done(app.handle("POST", "/api/up", {}))
    session._sleep = lambda _s: None
    result = done(app.handle("POST", "/api/sync", {}))
    assert result["speakers"][1]["lag_ms"] == pytest.approx(12.0, abs=0.3)
    assert result["speakers"][1]["mic_lag_ms"] == pytest.approx(12.0, abs=0.3) and result["distance_corrected"] is False
    assert result["speakers"][0]["applied_ms"] == pytest.approx(12.0, abs=0.3)
    assert result["confidence"] > 4
    assert app.handle("GET", "/api/status", None)[1]["last_sync"]["spread_ms"] == pytest.approx(12.0, abs=0.3)


def test_jobs_run_in_background_and_capture_errors():
    from soundscape.errors import AudioError
    jobs = Jobs()
    seen = []
    job = jobs.start("slow", lambda j: seen.append(1) or "value")
    for _ in range(100):
        if job.state == "done":
            break
        __import__("time").sleep(0.01)
    assert job.state == "done" and job.result == "value" and seen == [1]

    def boom(j):
        raise AudioError("nope", "fix it")

    job2 = jobs.start("bad", boom)
    for _ in range(100):
        if job2.state == "error":
            break
        __import__("time").sleep(0.01)
    assert job2.error == "nope" and job2.hint == "fix it"
    job3 = Jobs(synchronous=True).start("crash", lambda j: 1 / 0)
    assert job3.state == "error" and "ZeroDivisionError" in job3.error
    assert jobs.get("missing") is None


def test_bad_requests(session, world):
    app = app_for(session)
    assert app.handle("POST", "/api/volume", {"target": "all"})[0] == 400
    status, body, _ = app.handle("POST", "/api/volume", {"target": "ghost", "percent": 5})
    assert status == 400 and "hint" in body
    assert app.handle("POST", "/api/unknown", {})[0] == 404
    assert app.handle("POST", "/api/offset", {"target": "right", "delta_ms": "x"})[0] == 400


def test_mute_toggle_for_unknown_target_uses_memory(session, world):
    app = app_for(session)
    assert app._current_mute("ghost") is False
    app._mute_state["ghost"] = True
    assert app._current_mute("ghost") is True
    assert app._current_mute(MAC_B) is False


def test_token_required_when_configured(session, world):
    app = app_for(session, token="s3cret")
    assert app.handle("GET", "/api/status", None)[0] == 401
    assert app.handle("GET", "/api/status?token=wrong", None)[0] == 401
    assert app.handle("GET", "/api/status?token=s3cret", None)[0] == 200
    assert app.handle("GET", "/api/status", None, {"X-Soundscape-Token": "s3cret"})[0] == 200
    assert app.handle("POST", "/api/volume", {"target": "all", "percent": 5}, {"X-Soundscape-Token": "nope"})[0] == 401
    assert RemoteApp(session, log=lambda _m: None, token="").authorized("/x", None)


def test_real_server_handles_concurrent_requests(session, world):
    session.up()
    app = app_for(session, token="t")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    results = []

    def hit(i):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/volume", method="POST",
                                     data=json.dumps({"target": "all", "percent": 10 + i}).encode(),
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
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5)
        assert exc.value.code == 401
        bad = urllib.request.Request(f"http://127.0.0.1:{port}/api/volume", method="POST", data=b"{bad",
                                     headers={"content-type": "application/json", "X-Soundscape-Token": "t"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(bad, timeout=5)
        assert exc.value.code == 400
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_distance_api(session, world):
    app = app_for(session)
    status, body, _ = app.handle("POST", "/api/distance", {"target": "left", "distance": "4 ft"})
    assert status == 200 and body["distance_m"] == pytest.approx(1.219, abs=0.001)
    assert body["acoustic_ms"] == pytest.approx(3.55, abs=0.01) and body["distances"]["right"] is None
    assert app.handle("GET", "/api/status", None)[1]["speakers"][0]["distance_m"] == pytest.approx(1.219, abs=0.001)
    status, body, _ = app.handle("POST", "/api/distance", {"target": "left", "distance": "far"})
    assert status == 400 and "not a distance" in body["error"]
    status, body, _ = app.handle("POST", "/api/distance", {"target": "left", "distance": ""})
    assert status == 200 and body["distance_m"] is None


def test_remote_info_lists_phone_addresses(session, world, monkeypatch):
    from soundscape import remote as remote_mod
    monkeypatch.setattr(remote_mod, "lan_addresses", lambda: ["192.168.1.5"])
    monkeypatch.setattr(remote_mod, "host_name", lambda: "yogis-mac.local")
    app = app_for(session, token="abc")
    info = app.handle("GET", "/api/status", None, {"X-Soundscape-Token": "abc"})[1]["remote"]
    assert {k: info[k] for k in ("enabled", "lan", "port", "token_set", "urls", "qr")} == {"enabled": False, "lan": False, "port": None, "token_set": True, "urls": [], "qr": None}
    app.bind = ("0.0.0.0", 8787)
    info = app.handle("GET", "/api/status", None, {"X-Soundscape-Token": "abc"})[1]["remote"]
    assert info["lan"] and not info["enabled"] and info["urls"] == []  # reachable, but the switch is off
    session.config.remote = True
    info = app.handle("GET", "/api/status", None, {"X-Soundscape-Token": "abc"})[1]["remote"]
    assert info["enabled"] and info["lan"] and info["port"] == 8787 and info["token_set"]
    code = info["pairing"]["code"]  # phone-remote mode: the address carries the single-use code, never the master token
    assert info["urls"] == [f"http://yogis-mac.local:8787/?pair={code}", f"http://192.168.1.5:8787/?pair={code}"]
    assert info["qr"] == f"http://192.168.1.5:8787/?pair={code}"
    app.token = None
    assert app.handle("GET", "/api/status", None)[1]["remote"]["urls"][1].startswith("http://192.168.1.5:8787/?pair=")
    assert app.handle("POST", "/api/config", {"key": "remote", "value": False})[1]["value"] is False
    assert session.config.remote is False


def test_pairing_one_phone_at_a_time(session, world, monkeypatch):
    from soundscape import remote as remote_mod
    monkeypatch.setattr(remote_mod, "lan_addresses", lambda: ["192.168.1.5"])
    monkeypatch.setattr(remote_mod, "host_name", lambda: "mac.local")
    session.config.remote = True
    now = [1000.0]
    app = RemoteApp(session, log=lambda _m: None, synchronous_jobs=True, token="master", clock=lambda: now[0])
    app.bind = ("0.0.0.0", 8787)
    owner = {"X-Soundscape-Token": "master"}
    # the page itself is public so a phone can load it; every API call is checked
    assert app.handle("GET", "/", None, None, "192.168.1.20")[0] == 200
    status, body, _ = app.handle("GET", "/api/status", None, None, "192.168.1.20")
    assert status == 401 and "not paired" in body["error"]
    assert app.handle("GET", "/api/status", None, owner, "192.168.1.20")[0] == 401  # the master token is not a phone's key
    st = app.handle("GET", "/api/status", None, owner)[1]
    code = st["remote"]["pairing"]["code"]
    assert st["access"] == "owner" and len(code) == 6 and st["remote"]["qr"] == f"http://192.168.1.5:8787/?pair={code}"
    assert app.handle("POST", "/api/pair", {"code": "NOPE22", "device_id": "phone-a", "label": "iPhone"}, None, "192.168.1.20")[0] == 400
    status, body, _ = app.handle("POST", "/api/pair", {"code": code.lower(), "device_id": "phone-a", "label": "iPhone"}, None, "192.168.1.20")
    assert status == 200
    a = {"X-Soundscape-Device": "phone-a", "X-Soundscape-Token": body["token"]}
    st = app.handle("GET", "/api/status", None, a, "192.168.1.20")[1]
    assert st["access"] == "control" and "pairing" not in st["remote"] and st["remote"]["devices"] == []
    assert app.handle("POST", "/api/offset", {"target": "left", "delta_ms": 1}, a, "192.168.1.20")[0] == 200
    assert app.handle("GET", "/api/pairing", None, a, "192.168.1.20")[0] == 403
    # the code was consumed: the owner sees a new one and the old one no longer pairs
    code2 = app.handle("GET", "/api/status", None, owner)[1]["remote"]["pairing"]["code"]
    assert code2 != code
    assert app.handle("POST", "/api/pair", {"code": code, "device_id": "phone-b", "label": "Android"}, None, "192.168.1.21")[0] == 400
    body = app.handle("POST", "/api/pair", {"code": code2, "device_id": "phone-b", "label": "Android"}, None, "192.168.1.21")[1]
    b = {"X-Soundscape-Device": "phone-b", "X-Soundscape-Token": body["token"]}
    # phone A is active, so phone B may look but not touch
    st = app.handle("GET", "/api/status", None, b, "192.168.1.21")[1]
    assert st["access"] == "view" and st["controller"]["label"] == "iPhone"
    status, body, _ = app.handle("POST", "/api/offset", {"target": "left", "delta_ms": 1}, b, "192.168.1.21")
    assert status == 423 and body["controller"]["id"] == "phone-a"
    assert app.handle("POST", "/api/takeover", {}, b, "192.168.1.21")[0] == 200
    assert app.handle("GET", "/api/status", None, b, "192.168.1.21")[1]["access"] == "control"
    assert app.handle("POST", "/api/offset", {"target": "left", "delta_ms": 1}, a, "192.168.1.20")[0] == 423
    devices = app.handle("GET", "/api/status", None, owner)[1]["remote"]["devices"]
    assert [(d["id"], d["in_control"]) for d in devices] == [("phone-a", False), ("phone-b", True)]
    # B goes quiet; after the idle timeout A gets control back by itself
    now[0] += 30
    assert app.handle("GET", "/api/status", None, a, "192.168.1.20")[1]["access"] == "control"
    assert app.handle("GET", "/api/status", None, b, "192.168.1.21")[1]["access"] == "view"
    # paired phones survive a restart; only the owner can forget one
    app2 = RemoteApp(session, log=lambda _m: None, synchronous_jobs=True, token="master", clock=lambda: now[0])
    app2.bind = ("0.0.0.0", 8787)
    assert app2.handle("GET", "/api/status", None, a, "192.168.1.20")[1]["access"] == "control"
    assert app2.handle("POST", "/api/forget", {"device_id": "phone-a"}, a, "192.168.1.20")[0] == 403
    assert app2.handle("POST", "/api/forget", {"device_id": "phone-a"}, owner)[1]["ok"] is True
    assert app2.handle("GET", "/api/status", None, a, "192.168.1.20")[0] == 401
    assert app2.handle("GET", "/api/status", None, b, "192.168.1.21")[1]["access"] == "control"
    # ten wrong guesses rotate the code
    code3 = app2.handle("GET", "/api/status", None, owner)[1]["remote"]["pairing"]["code"]
    for _ in range(10):
        app2.handle("POST", "/api/pair", {"code": "WRONG2", "device_id": "x", "label": "x"}, None, "192.168.1.30")
    assert app2.handle("GET", "/api/status", None, owner)[1]["remote"]["pairing"]["code"] != code3
    # an expired code is refused too
    code4 = app2.handle("GET", "/api/status", None, owner)[1]["remote"]["pairing"]["code"]
    now[0] += 700
    assert app2.handle("POST", "/api/pair", {"code": code4, "device_id": "phone-c", "label": "iPad"}, None, "192.168.1.22")[0] == 400


def test_phone_remote_switch_works_live(session, world, monkeypatch):
    from soundscape import remote as remote_mod
    monkeypatch.setattr(remote_mod, "lan_addresses", lambda: ["192.168.1.5"])
    monkeypatch.setattr(remote_mod, "host_name", lambda: "mac.local")
    session.config.remote = False
    app = app_for(session, token="s3cret")
    app.bind = ("0.0.0.0", 8787)
    owner = {"X-Soundscape-Token": "s3cret"}
    # switched off: the network gets a plain "it is off" page and nothing else, token or not
    status, body, ctype = app.handle("GET", "/", None, None, "192.168.1.9")
    assert status == 403 and "phone remote is off" in body and "html" in ctype
    assert app.handle("GET", "/api/status", None, owner, "192.168.1.9")[0] == 403
    assert app.handle("POST", "/api/pair", {"code": "ABCDEF", "device_id": "p", "label": "x"}, None, "192.168.1.9")[0] == 403
    info = app.handle("GET", "/api/status", None, owner)[1]["remote"]
    assert info["lan"] and not info["enabled"] and info["urls"] == [] and "pairing" not in info
    assert app.handle("GET", "/api/status", None, None, "127.0.0.1")[0] == 401  # the Mac still needs its token
    # the owner flips the switch: phones can pair at once, no restart
    assert app.handle("POST", "/api/config", {"key": "remote", "value": True}, owner)[1]["value"] is True
    code = app.handle("GET", "/api/status", None, owner)[1]["remote"]["pairing"]["code"]
    tok = app.handle("POST", "/api/pair", {"code": code, "device_id": "p1", "label": "iPhone"}, None, "192.168.1.9")[1]["token"]
    p1 = {"X-Soundscape-Device": "p1", "X-Soundscape-Token": tok}
    assert app.handle("GET", "/api/status", None, p1, "192.168.1.9")[1]["access"] == "control"
    # off again: the phone is shut out immediately but stays known
    assert app.handle("POST", "/api/config", {"key": "remote", "value": False}, owner)[1]["value"] is False
    assert app.handle("GET", "/api/status", None, p1, "192.168.1.9")[0] == 403
    assert app.remotes.controller == "p1" and "p1" in app.remotes.devices  # remembered, so it resumes when the switch returns
    assert app.handle("POST", "/api/config", {"key": "remote", "value": True}, owner)[1]["value"] is True
    assert app.handle("GET", "/api/status", None, p1, "192.168.1.9")[1]["access"] == "control"

"""Phone-friendly web remote (stdlib only): status, volumes, mute, sync offsets, media keys.

Binds to 127.0.0.1 unless ``--host 0.0.0.0`` is given. With ``--token`` every request must
carry the token (``?token=`` on the first page load, then an ``X-Soundscape-Token`` header the
page adds itself); without it, only expose the remote on a network you trust.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .errors import SoundscapeError
from .session import Session

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Soundscape</title>
<style>
:root{--bg:#0f1115;--fg:#e8eaf0;--mut:#8b93a7;--acc:#5ad1a5;--card:#171a21;--line:#262b36}
@media (prefers-color-scheme: light){:root{--bg:#f6f7fa;--fg:#13161c;--mut:#5d6578;--acc:#0f9d6e;--card:#fff;--line:#dde1ea}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.4 system-ui,sans-serif}
main{max-width:560px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:8px 0 16px;display:flex;align-items:center;gap:10px}
.dot{width:10px;height:10px;border-radius:50%;background:#e05d5d}.dot.on{background:var(--acc)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:12px}
.row{display:flex;justify-content:space-between;align-items:center;gap:12px}
.sub{color:var(--mut);font-size:13px}
input[type=range]{width:100%;accent-color:var(--acc)}
button{background:transparent;color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px 12px;font-size:15px;cursor:pointer}
button.pri{background:var(--acc);color:#06120d;border-color:var(--acc)}
.keys{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px}
.off{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.mono{font-variant-numeric:tabular-nums}
#msg{min-height:1.2em;color:var(--mut);font-size:13px}
</style></head><body><main>
<h1><span class="dot" id="dot"></span>Soundscape <span class="sub" id="eng"></span></h1>
<div class="card">
  <div class="row"><strong>Both speakers</strong><span class="mono" id="mv"></span></div>
  <input type="range" min="0" max="100" id="master" oninput="vol('all',this.value)">
  <div class="keys">
    <button onclick="media('previous')">&#9664;&#9664;</button>
    <button class="pri" onclick="media('play-pause')">&#9654; / &#10074;&#10074;</button>
    <button onclick="media('next')">&#9654;&#9654;</button>
  </div>
</div>
<div id="speakers"></div>
<div class="card row"><button onclick="act('up')">Bring up</button><button onclick="act('down')">Take down</button><button onclick="act('test')">Test tones</button></div>
<div id="msg"></div>
</main>
<script>
let busy=false;
const TOKEN=new URLSearchParams(location.search).get('token')||'';
async function api(path,body){const h={'X-Soundscape-Token':TOKEN};if(body)h['content-type']='application/json';const r=await fetch(path,body?{method:'POST',headers:h,body:JSON.stringify(body)}:{headers:h});const j=await r.json();if(!r.ok){say(j.error||'error')}return j}
function say(t){document.getElementById('msg').textContent=t||''}
async function refresh(){if(busy)return;try{const s=await api('/api/status');render(s)}catch(e){say('offline')}}
function render(s){document.getElementById('dot').className='dot'+(s.up?' on':'');document.getElementById('eng').textContent=s.up?('via '+s.engine):'down';
 if(s.volume!=null&&document.activeElement.id!=='master'){document.getElementById('master').value=s.volume;document.getElementById('mv').textContent=s.volume+'%'}
 const box=document.getElementById('speakers');const ids=new Set();
 for(const sp of s.speakers){ids.add(sp.address);let el=document.getElementById('sp-'+sp.address.replace(/:/g,''));
  if(!el){el=document.createElement('div');el.className='card';el.id='sp-'+sp.address.replace(/:/g,'');el.innerHTML=`<div class="row"><div><strong>${sp.position}</strong> <span class="sub">${sp.name}</span></div><span class="sub st"></span></div>
  <input type="range" min="0" max="100" class="v" oninput="vol('${sp.address}',this.value)">
  <div class="row"><span class="sub">delay <b class="mono o"></b> ms</span><span class="off">
  <button onclick="off('${sp.address}',-5)">-5</button><button onclick="off('${sp.address}',-1)">-1</button><button onclick="off('${sp.address}',1)">+1</button><button onclick="off('${sp.address}',5)">+5</button>
  <button onclick="mute('${sp.address}')">mute</button></span></div>`;box.appendChild(el)}
  el.querySelector('.st').textContent=sp.connected?(sp.muted?'muted':'connected'):'disconnected';
  const v=el.querySelector('.v');if(sp.volume!=null&&document.activeElement!==v)v.value=sp.volume;
  el.querySelector('.o').textContent=(sp.offset_ms||0).toFixed(1)}
}
let vt={};function vol(t,v){clearTimeout(vt[t]);vt[t]=setTimeout(()=>api('/api/volume',{target:t,percent:+v}).then(refresh),120)}
function off(t,d){api('/api/offset',{target:t,delta_ms:d}).then(refresh)}
function mute(t){api('/api/mute',{target:t,toggle:true}).then(refresh)}
function media(a){api('/api/media',{action:a})}
async function act(a){busy=true;say(a+' ...');try{const r=await api('/api/'+a,{});say(r.message||'')}finally{busy=false;refresh()}}
refresh();setInterval(refresh,3000);
</script></body></html>
"""


class RemoteApp:
    """HTTP-agnostic request handling, so it can be unit tested without sockets."""

    def __init__(self, session: Session, log: Callable[[str], None] = print, token: Optional[str] = None):
        self.session = session
        self.log = log
        self.token = token or None
        self._lock = threading.Lock()
        self._mute_state: Dict[str, bool] = {}

    def authorized(self, path: str, headers: Optional[Dict[str, str]]) -> bool:
        if not self.token:
            return True
        supplied = (headers or {}).get("X-Soundscape-Token") or (parse_qs(urlparse(path).query).get("token") or [""])[0]
        import hmac
        return hmac.compare_digest(supplied, self.token)

    def handle(self, method: str, path: str, body: Optional[Dict[str, Any]],
               headers: Optional[Dict[str, str]] = None) -> Tuple[int, Any, str]:
        """Return (status, payload, content_type)."""
        route = urlparse(path).path.rstrip("/") or "/"
        if not self.authorized(path, headers):
            return 401, {"error": "token required", "hint": "open the remote with ?token=... as printed when it started"}, "application/json"
        try:
            if method == "GET" and route == "/":
                return 200, PAGE, "text/html; charset=utf-8"
            if method == "GET" and route == "/api/status":
                with self._lock:
                    return 200, self.session.status(), "application/json"
            if method != "POST":
                return 404, {"error": "not found"}, "application/json"
            body = body or {}
            with self._lock:
                return 200, self._post(route, body), "application/json"
        except SoundscapeError as exc:
            return 400, {"error": exc.message, "hint": exc.hint}, "application/json"
        except (KeyError, TypeError, ValueError) as exc:
            return 400, {"error": f"bad request: {exc}"}, "application/json"

    def _post(self, route: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if route == "/api/volume":
            sink = self.session.set_volume(str(body["target"]), int(body["percent"]))
            return {"ok": True, "sink": sink}
        if route == "/api/mute":
            target = str(body["target"])
            if body.get("toggle"):
                mute = not self._current_mute(target)
            else:
                mute = bool(body.get("mute", True))
            self.session.set_mute(target, mute)
            self._mute_state[target] = mute
            return {"ok": True, "muted": mute}
        if route == "/api/offset":
            target = str(body["target"])
            if "delta_ms" in body:
                sp = self.session.set_offset(target, float(body["delta_ms"]), relative=True)
            else:
                sp = self.session.set_offset(target, float(body["offset_ms"]))
            return {"ok": True, "offset_ms": sp.offset_ms}
        if route == "/api/media":
            return self._media(str(body.get("action", "play-pause")))
        if route == "/api/up":
            self.session.up()
            return {"ok": True, "message": "Soundscape is up"}
        if route == "/api/down":
            self.session.down()
            return {"ok": True, "message": "Soundscape is down"}
        if route == "/api/test":
            from .cli import play_position_tones
            play_position_tones(self.session)
            return {"ok": True, "message": "played test tones"}
        raise KeyError(route)

    def _current_mute(self, target: str) -> bool:
        status = self.session.status()
        if target in ("all", "master", "both"):
            return bool(status.get("muted"))
        for sp in status["speakers"]:
            if target in (sp["address"], sp["position"]):
                return bool(sp.get("muted"))
        return self._mute_state.get(target, False)

    def _media(self, action: str) -> Dict[str, Any]:
        if action not in ("play-pause", "play", "pause", "next", "previous", "stop"):
            raise ValueError(f"unknown media action {action}")
        runner = self.session.runner
        if not runner.which("playerctl"):
            return {"ok": False, "message": "install playerctl for media keys"}
        res = runner.run(["playerctl", action], timeout=5)
        return {"ok": res.ok, "message": (res.stderr or "").strip()}


def make_handler(app: RemoteApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Soundscape/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default
            return None

        def _send(self, status: int, payload: Any, ctype: str) -> None:
            data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _headers(self) -> Dict[str, str]:
            return {"X-Soundscape-Token": self.headers.get("X-Soundscape-Token", "")}

        def do_GET(self) -> None:  # noqa: N802
            self._send(*app.handle("GET", self.path, None, self._headers()))

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._send(400, {"error": "invalid JSON"}, "application/json")
                return
            self._send(*app.handle("POST", self.path, body, self._headers()))

    return Handler


def serve(session: Session, host: str = "127.0.0.1", port: int = 8787, log: Callable[[str], None] = print,
          token: Optional[str] = None) -> None:
    app = RemoteApp(session, log, token=token)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    suffix = f"?token={token}" if token else ""
    log(f"Soundscape remote on http://{host}:{port}/{suffix}  (Ctrl-C to stop)")
    if host == "127.0.0.1":
        log("only this machine can reach it; use --host 0.0.0.0 to control it from your phone")
    elif not token:
        log("warning: anyone on this network can change volumes; add --token SECRET to require a token")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

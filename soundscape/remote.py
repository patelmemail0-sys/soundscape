"""Local web UI and phone remote (stdlib only).

``soundscape ui`` serves a single-page dashboard that shows the live signal path, every
speaker's state, calibration results and the doctor checks, and lets you control all of it.
Long operations (scan, calibrate, up/down, add) run as background jobs so the page keeps
updating while they work. Quick controls (volume, mute, trim) are applied inline.

Binds to 127.0.0.1 unless ``--host 0.0.0.0`` is given. With ``--token`` every request must
carry the token (``?token=`` on the first page load, then an ``X-Soundscape-Token`` header the
page adds itself); without it, only expose the UI on a network you trust.
"""

from __future__ import annotations

import hmac
import json
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import __version__
from .errors import SoundscapeError
from .session import Session

Log = Callable[[str], None]
UI_FILE = Path(__file__).with_name("ui.html")


def load_page() -> str:
    return UI_FILE.read_text(encoding="utf-8")


PAGE = load_page()  # kept for tests and backwards compatibility


# -- background jobs ---------------------------------------------------------------

@dataclass
class Job:
    id: str
    name: str
    state: str = "queued"  # queued | running | done | error
    log: List[str] = field(default_factory=list)
    result: Any = None
    error: Optional[str] = None
    hint: Optional[str] = None
    started: float = 0.0
    finished: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Jobs:
    """One worker thread; jobs run strictly one after another (they all touch the sound server)."""

    def __init__(self, synchronous: bool = False):
        self.synchronous = synchronous
        self._jobs: Dict[str, Job] = {}
        self._queue: Deque[Tuple[Job, Callable[[Job], Any]]] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self.current: Optional[Job] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, name: str, fn: Callable[[Job], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex[:8], name=name)
        with self._lock:
            self._jobs[job.id] = job
            if len(self._jobs) > 50:  # forget the oldest finished jobs
                for old in [j for j in list(self._jobs.values()) if j.state in ("done", "error")][:-20]:
                    self._jobs.pop(old.id, None)
        if self.synchronous:
            self._run(job, fn)
            return job
        with self._lock:
            self._queue.append((job, fn))
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._worker, daemon=True)
                self._thread.start()
            self._wake.notify()
        return job

    def _worker(self) -> None:
        while True:
            with self._lock:
                while not self._queue:
                    if not self._wake.wait(timeout=30):
                        return
                job, fn = self._queue.popleft()
            self._run(job, fn)

    def _run(self, job: Job, fn: Callable[[Job], Any]) -> None:
        job.state, job.started = "running", time.time()
        self.current = job
        try:
            job.result = fn(job)
            job.state = "done"
        except SoundscapeError as exc:
            job.state, job.error, job.hint = "error", exc.message, exc.hint or None
        except Exception as exc:  # a bug, but the UI must keep working
            job.state, job.error = "error", f"{type(exc).__name__}: {exc}"
        finally:
            job.finished = time.time()
            self.current = None

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    @property
    def active(self) -> Optional[Job]:
        with self._lock:
            if self.current:
                return self.current
            return self._queue[0][0] if self._queue else None


class RouteNotFound(Exception):
    pass


# -- application ----------------------------------------------------------------------

class RemoteApp:
    """HTTP-agnostic request handling, so it can be unit tested without sockets."""

    def __init__(self, session: Session, log: Log = print, token: Optional[str] = None,
                 synchronous_jobs: bool = False):
        self.session = session
        self.log_out = log
        self.token = token or None
        self._lock = threading.Lock()
        self._mute_state: Dict[str, bool] = {}
        self.events: Deque[Dict[str, Any]] = deque(maxlen=300)
        self.jobs = Jobs(synchronous=synchronous_jobs)
        self.last_sync: Optional[Dict[str, Any]] = None
        self._job_local = threading.local()
        session.log = self._log  # capture backend progress for the activity log and job logs

    # -- logging ---------------------------------------------------------------------
    def _log(self, msg: str) -> None:
        entry = {"t": time.time(), "msg": msg}
        self.events.append(entry)
        job = getattr(self._job_local, "job", None)
        if job is not None:
            job.log.append(msg)
        try:
            self.log_out(msg)
        except Exception:
            pass

    def _run_job(self, name: str, fn: Callable[[], Any]) -> Dict[str, Any]:
        def wrapper(job: Job) -> Any:
            self._job_local.job = job
            try:
                with self._lock:
                    return fn()
            finally:
                self._job_local.job = None
        job = self.jobs.start(name, wrapper)
        return {"job": job.to_dict()}

    # -- auth ------------------------------------------------------------------------
    def authorized(self, path: str, headers: Optional[Dict[str, str]]) -> bool:
        if not self.token:
            return True
        supplied = (headers or {}).get("X-Soundscape-Token") or (parse_qs(urlparse(path).query).get("token") or [""])[0]
        return hmac.compare_digest(supplied, self.token)

    # -- routing -----------------------------------------------------------------------
    def handle(self, method: str, path: str, body: Optional[Dict[str, Any]],
               headers: Optional[Dict[str, str]] = None) -> Tuple[int, Any, str]:
        """Return (status, payload, content_type)."""
        parsed = urlparse(path)
        route = parsed.path.rstrip("/") or "/"
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if not self.authorized(path, headers):
            return 401, {"error": "token required", "hint": "open the UI with ?token=... as printed when it started"}, "application/json"
        try:
            if method == "GET":
                if route == "/":
                    return 200, load_page(), "text/html; charset=utf-8"
                return 200, self._get(route, query), "application/json"
            if method == "POST":
                return 200, self._post(route, body or {}), "application/json"
            return 404, {"error": "not found"}, "application/json"
        except RouteNotFound as exc:
            return 404, {"error": f"unknown route {exc}"}, "application/json"
        except SoundscapeError as exc:
            return 400, {"error": exc.message, "hint": exc.hint}, "application/json"
        except KeyError as exc:
            return 400, {"error": f"bad request: missing field {exc}"}, "application/json"
        except (TypeError, ValueError) as exc:
            return 400, {"error": f"bad request: {exc}"}, "application/json"

    # -- GET -------------------------------------------------------------------------
    def _get(self, route: str, query: Dict[str, str]) -> Any:
        if route == "/api/status":
            return self.status()
        if route == "/api/devices":
            return [self._device(d) for d in self.session.bluez.devices()]
        if route == "/api/sources":
            try:
                sources = self.session.pulse.sources()
            except SoundscapeError:
                sources = []
            speaker_macs = [sp.address for sp in self.session.config.speakers]
            picker = getattr(self.session.pulse, "pick_microphone", None)
            recommended = picker(speaker_macs) if picker else None
            for src in sources:
                addr = (src.get("address") or "").upper()
                src["speaker_mic"] = bool(addr and addr in speaker_macs)
            return {"recommended": recommended, "sources": sources}
        if route == "/api/doctor":
            from .doctor import run_checks
            with self._lock:
                checks = run_checks(self.session.runner, self.session.bluez, self.session.pulse)
            return [{"status": c.status, "title": c.title, "detail": c.detail, "fix": c.fix} for c in checks]
        if route == "/api/log":
            return list(self.events)[-int(query.get("n", "80")):]
        if route == "/api/job":
            job = self.jobs.get(query.get("id", ""))
            if job is None:
                raise RouteNotFound("job")
            return job.to_dict()
        raise RouteNotFound(route)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            st = self.session.status()
        cfg = self.session.config
        st["config"] = {k: getattr(cfg, k) for k in cfg.SETTABLE}
        st["version"] = __version__
        st["last_sync"] = self.last_sync
        active = self.jobs.active
        st["job"] = active.to_dict() if active else None
        st["engines"] = ["auto", "aggregate", "relay"] if st.get("platform") == "mac" else ["auto", "pipewire", "pulse"]
        return st

    @staticmethod
    def _device(d: Any) -> Dict[str, Any]:
        return {"address": d.address, "name": d.label, "paired": d.paired, "connected": d.connected,
                "is_audio": d.is_audio_sink, "rssi": d.rssi}

    # -- POST --------------------------------------------------------------------------
    def _post(self, route: str, body: Dict[str, Any]) -> Any:
        s = self.session
        if route == "/api/up":
            return self._run_job("Bringing speakers up", lambda: s.up().engine)
        if route == "/api/down":
            return self._run_job("Taking the output down", lambda: s.down())
        if route == "/api/scan":
            seconds = float(body.get("seconds", 10))
            return self._run_job("Scanning for speakers",
                                 lambda: [self._device(d) for d in s.scan(seconds, audio_only=not body.get("all"))])
        if route == "/api/add":
            address = str(body["address"])
            position = body.get("position") or None
            name = str(body.get("name") or "")
            return self._run_job(f"Adding {name or address}",
                                 lambda: asdict(s.add_speaker(address, position=position, name=name,
                                                              replace=bool(body.get("replace")), connect=True)))
        if route == "/api/remove":
            with self._lock:
                sp = s.remove_speaker(str(body["target"]), forget=bool(body.get("forget")))
            return {"ok": True, "removed": sp.label}
        if route == "/api/reconnect":
            target = str(body["target"])
            return self._run_job(f"Reconnecting {target}", lambda: asdict(s.reconnect_speaker(target)))
        if route == "/api/sync":
            return self._run_job("Calibrating with the microphone", lambda: self._calibrate(body))
        if route == "/api/test":
            from .cli import play_position_tones
            tone_target = body.get("target") or None

            def tones_job() -> bool:
                play_position_tones(s, only=tone_target, log=self._log)
                return True
            return self._run_job("Playing test tones", tones_job)
        if route == "/api/config":
            with self._lock:
                value = s.config.set_option(str(body["key"]), body["value"])
                s.save_config()
            return {"ok": True, "key": body["key"], "value": value,
                    "note": "run Up again to apply" if s.state() else None}
        with self._lock:
            if route == "/api/volume":
                sink = s.set_volume(str(body["target"]), int(body["percent"]))
                return {"ok": True, "sink": sink}
            if route == "/api/mute":
                target = str(body["target"])
                mute = (not self._current_mute(target)) if body.get("toggle") else bool(body.get("mute", True))
                s.set_mute(target, mute)
                self._mute_state[target] = mute
                return {"ok": True, "muted": mute}
            if route == "/api/offset":
                target = str(body["target"])
                if target in ("all", "reset"):
                    s.reset_offsets()
                    return {"ok": True, "offsets": {x.position: 0.0 for x in s.config.speakers}}
                if "delta_ms" in body:
                    sp = s.set_offset(target, float(body["delta_ms"]), relative=True)
                else:
                    sp = s.set_offset(target, float(body["offset_ms"]))
                return {"ok": True, "offset_ms": sp.offset_ms,
                        "offsets": {x.position: x.offset_ms for x in s.config.speakers}}
            if route == "/api/media":
                return self._media(str(body.get("action", "play-pause")))
        raise RouteNotFound(route)

    def _calibrate(self, body: Dict[str, Any]) -> Dict[str, Any]:
        from .sync import Calibrator
        cal = Calibrator(self.session, log=self._log)
        m = cal.auto(source=body.get("source") or None, apply=body.get("apply", True) is not False,
                     passes=int(body.get("passes", 1)))
        result = {
            "at": time.time(),
            "speakers": [{"address": sp.address, "position": sp.position, "name": sp.label,
                          "lag_ms": lag, "applied_ms": m.applied.get(sp.address, 0.0), "offset_ms": sp.offset_ms}
                         for sp, lag in zip(m.speakers, m.lag_ms)],
            "confidence": round(min(m.confidence, 99.0), 1), "spread_ms": round(m.spread_ms, 2),
        }
        self.last_sync = result
        return result

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


# -- HTTP glue -------------------------------------------------------------------------

def make_handler(app: RemoteApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"Soundscape/{__version__}"

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


def serve(session: Session, host: str = "127.0.0.1", port: int = 8787, log: Log = print,
          token: Optional[str] = None, open_browser: bool = False) -> None:
    app = RemoteApp(session, log, token=token)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    suffix = f"?token={token}" if token else ""
    shown_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    url = f"http://{shown_host}:{httpd.server_address[1]}/{suffix}"
    log(f"Soundscape UI on {url}  (Ctrl-C to stop)")
    if host == "127.0.0.1":
        log("only this machine can reach it; use --host 0.0.0.0 to control it from your phone")
    elif not token:
        log("warning: anyone on this network can change volumes; add --token SECRET to require a token")
    if open_browser:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

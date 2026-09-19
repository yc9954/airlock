"""A dependency-free HTTP server: static UI + streaming research labs (SSE).

stdlib only, so `python3 -m airlock` just runs. Endpoints (SPEC §2):

    POST /api/labs                {prompt, task?, budget?, seed?} -> {lab_id}
                                  starts a ResearchLoop in a worker thread
    GET  /api/labs                sidebar list [{id, title, last_message, updated, status}]
    GET  /api/labs/{id}/stream    Server-Sent Events, replayed from event 0 on connect
    POST /api/labs/{id}/steer     {text}             -> {ok, lab_id}   (steering message)
    POST /api/labs/{id}/answer    {ask_id, option_id} -> {ok, lab_id}   (answers an `ask`)
    GET  /api/health              providers (incl. runner label) + config
    GET  /                        web/dist/ (SPA fallback to index.html) when built,
                                  else the legacy console in web-legacy/

Legacy (the landing-page evolution pipeline), kept working:
    POST /api/run  {prompt,n?}    GET /api/stream?run_id=...   POST /api/select
    GET  /runs/<run>/...          serves variant + champion artifacts (iframes)
"""
import inspect
import json
import mimetypes
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import Config
from .pipeline import Pipeline

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "web", "dist")
WEB_LEGACY = os.path.join(ROOT, "web-legacy")


class Broker:
    """Buffers events so a late SSE subscriber still replays from 0."""
    KEEPALIVE_S = 15

    def __init__(self):
        self.events = []
        self.done = False
        self.cond = threading.Condition()

    def emit(self, e):
        with self.cond:
            e.setdefault("ts", round(time.time(), 3))
            e.setdefault("seq", len(self.events))
            self.events.append(e)
            if e.get("type") == "done":
                self.done = True
            self.cond.notify_all()

    def finish(self):
        with self.cond:
            self.done = True
            self.cond.notify_all()

    def stream(self):
        """Yields every event from 0, then live ones; yields None on a keepalive
        tick so the transport can write a comment and detect a dead client."""
        i = 0
        while True:
            with self.cond:
                if i >= len(self.events) and not self.done:
                    self.cond.wait(timeout=self.KEEPALIVE_S)
                batch = self.events[i:]
                i = len(self.events)
                done = self.done and i >= len(self.events)
            if not batch and not done:
                yield None
                continue
            for e in batch:
                yield e
            if done:
                return


# ------------------------------------------------------------------ labs --
def default_loop_factory(cfg, lab_id, prompt, task, budget, seed, emit):
    """Build the research loop lazily so the server boots while loop.py is still
    being written. Passes only the constructor kwargs the loop actually accepts."""
    from .research.loop import ResearchLoop          # ImportError -> 503 upstream
    candidates = {"cfg": cfg, "config": cfg, "lab_id": lab_id, "prompt": prompt,
                  "task": task, "budget": budget, "seed": seed, "emit": emit}
    try:
        params = inspect.signature(ResearchLoop).parameters
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
            kwargs = candidates
        else:
            kwargs = {k: v for k, v in candidates.items() if k in params}
    except (TypeError, ValueError):
        kwargs = candidates
    return ResearchLoop(**kwargs)


class Lab:
    def __init__(self, lab_id, prompt, task, budget, seed):
        self.id = lab_id
        self.prompt = prompt
        self.task = task
        self.budget = budget
        self.seed = seed
        self.created = time.time()
        self.broker = Broker()
        self.loop = None
        self.error = None

    def status(self):
        evs = self.broker.events
        if self.error and not evs:
            return "error"
        if evs and evs[-1].get("type") == "error":
            return "error"
        if self.broker.done:
            return "done"
        if evs and evs[-1].get("type") == "ask":
            return "waiting"
        return "running"

    def summary(self):
        evs = self.broker.events
        title = self.prompt[:80] or "Untitled lab"
        last = ""
        for e in evs:
            t = e.get("type")
            if t == "lab_started" and e.get("task"):
                task = e["task"]
                title = (task.get("desc") or task.get("dataset") or title) if isinstance(task, dict) else str(task)
            if t in ("agent_message", "user_message") and e.get("text"):
                last = e["text"]
            elif t == "ask" and e.get("question"):
                last = e["question"]
            elif t == "champion" and e.get("spec") is not None:
                last = f"Champion → {e.get('metric')}"
            elif t == "done" and e.get("summary"):
                last = str(e["summary"])
            elif t == "error":
                last = f"Error: {e.get('message', '')}"
        updated = evs[-1]["ts"] if evs else self.created
        return {"id": self.id, "title": title, "last_message": last[:200],
                "updated": updated, "created": self.created, "status": self.status(),
                "events": len(evs)}


class App:
    def __init__(self, cfg, loop_factory=None):
        self.cfg = cfg
        self.base_url = f"http://{cfg.host}:{cfg.port}"
        self.pipeline = Pipeline(cfg, self.base_url)
        self.brokers = {}                                # legacy runs
        self.labs = {}                                   # lab_id -> Lab
        self.loop_factory = loop_factory or default_loop_factory
        self._lock = threading.Lock()

    # ---- legacy evolution runs ----
    def start_run(self, prompt, n):
        run_id = uuid.uuid4().hex[:10]
        if n:
            self.cfg.n_generate = max(4, min(120, int(n)))
        broker = Broker()
        self.brokers[run_id] = broker

        def work():
            try:
                self.pipeline.run_evolution(run_id, prompt, broker.emit)
            except Exception as exc:
                broker.emit({"type": "error", "message": str(exc)})
            finally:
                broker.finish()

        threading.Thread(target=work, daemon=True).start()
        return run_id

    # ---- research labs ----
    def start_lab(self, prompt, task=None, budget=None, seed=None):
        lab_id = uuid.uuid4().hex[:10]
        lab = Lab(lab_id, prompt, task, budget, seed)
        try:
            lab.loop = self.loop_factory(self.cfg, lab_id, prompt, task, budget, seed,
                                         lab.broker.emit)
        except Exception as exc:
            raise RuntimeError(f"research loop unavailable: {type(exc).__name__}: {exc}")
        with self._lock:
            self.labs[lab_id] = lab

        def work():
            try:
                lab.loop.run()
            except Exception as exc:
                lab.error = str(exc)
                lab.broker.emit({"type": "error", "message": str(exc)})
            finally:
                lab.broker.finish()

        threading.Thread(target=work, name=f"lab-{lab_id}", daemon=True).start()
        return lab_id

    def steer(self, lab_id, text):
        lab = self.labs.get(lab_id)
        if not lab:
            raise KeyError(lab_id)
        if not hasattr(lab.loop, "steer"):
            raise RuntimeError("this loop does not accept steering")
        lab.broker.emit({"type": "user_message", "text": text})
        return lab.loop.steer(text)

    def answer(self, lab_id, ask_id, option_id):
        lab = self.labs.get(lab_id)
        if not lab:
            raise KeyError(lab_id)
        if not hasattr(lab.loop, "answer"):
            raise RuntimeError("this loop does not accept answers")
        lab.broker.emit({"type": "answered", "ask_id": ask_id, "option_id": option_id})
        return lab.loop.answer(ask_id, option_id)

    def list_labs(self):
        with self._lock:
            labs = list(self.labs.values())
        return sorted((lab.summary() for lab in labs), key=lambda s: -s["updated"])

    def dist_dir(self):
        """web/dist when it has been built (tests may point elsewhere), else None."""
        d = getattr(self, "_dist_override", None) or DIST
        return d if os.path.isfile(os.path.join(d, "index.html")) else None

    def health(self):
        labels = dict(self.cfg.provider_labels())
        try:
            from .research.runners import runner_label
            labels["runner"] = runner_label(self.cfg)
        except Exception:
            labels["runner"] = "local"
        try:
            import importlib
            importlib.import_module("airlock.research.loop")
            loop_ok = True
        except Exception:
            loop_ok = False
        return {
            "ok": True, "providers": labels,
            "config": {"host": self.cfg.host, "port": self.cfg.port,
                       "cpus": os.cpu_count(), "daytona": bool(self.cfg.daytona_key)},
            "frontend": "dist" if os.path.exists(os.path.join(DIST, "index.html")) else "legacy",
            "research_loop": loop_ok,
            "labs": len(self.labs),
            # legacy pipeline shape, still reported for the old console
            "n": self.cfg.n_generate, "k": self.cfg.k_finalists,
            "generations": self.cfg.generations,
        }


# --------------------------------------------------------------- handler --
def _handler(app):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):        # quiet; the agent narrates instead
            pass

        # ---- helpers ----
        def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _file(self, path, ctype=None, cache=False):
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                return self._send(404, b"not found")
            ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            extra = {"Cache-Control": "public, max-age=31536000, immutable"} if cache \
                else {"Cache-Control": "no-cache"}
            self._send(200, data, ctype, extra)

        def _read_json(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        # ---- routing ----
        def do_GET(self):
            p = self.path.split("?", 1)[0]
            if p == "/api/health":
                return self._json(200, app.health())
            if p == "/api/labs":
                return self._json(200, app.list_labs())
            if p.startswith("/api/labs/"):
                parts = p.split("/")            # ['', 'api', 'labs', id, 'stream']
                if len(parts) == 5 and parts[4] == "stream":
                    lab = app.labs.get(parts[3])
                    if not lab:
                        return self._json(404, {"error": "no such lab"})
                    return self._sse(lab.broker)
                return self._json(404, {"error": "not found"})
            if p == "/api/stream":
                return self._legacy_stream()
            if p.startswith("/api/"):
                return self._json(404, {"error": "not found"})
            if p.startswith("/runs/"):
                return self._serve_run(p)
            return self._static(p)

        def do_POST(self):
            p = self.path.split("?", 1)[0]
            if p == "/api/labs":
                d = self._read_json()
                prompt = (d.get("prompt") or "").strip() or "Find the best composite recipe"
                try:
                    lab_id = app.start_lab(prompt, d.get("task"), d.get("budget"), d.get("seed"))
                except RuntimeError as exc:
                    return self._json(503, {"error": str(exc)})
                return self._json(200, {"lab_id": lab_id})
            if p.startswith("/api/labs/"):
                parts = p.split("/")
                if len(parts) == 5 and parts[4] in ("steer", "answer"):
                    d = self._read_json()
                    try:
                        if parts[4] == "steer":
                            text = (d.get("text") or "").strip()
                            if not text:
                                return self._json(400, {"error": "text required"})
                            ack = app.steer(parts[3], text)
                        else:
                            if not d.get("ask_id") or d.get("option_id") is None:
                                return self._json(400, {"error": "ask_id and option_id required"})
                            ack = app.answer(parts[3], d["ask_id"], d["option_id"])
                    except KeyError:
                        return self._json(404, {"error": "no such lab"})
                    except Exception as exc:
                        return self._json(400, {"error": str(exc)})
                    body = {"ok": True, "lab_id": parts[3]}
                    if isinstance(ack, dict):
                        body.update(ack)
                    elif ack is not None:
                        body["ack"] = ack
                    return self._json(200, body)
                return self._json(404, {"error": "not found"})
            if p == "/api/run":
                d = self._read_json()
                prompt = (d.get("prompt") or "").strip() or "Landing page for a new AI product"
                run_id = app.start_run(prompt, d.get("n"))
                return self._json(200, {"run_id": run_id})
            if p == "/api/select":
                d = self._read_json()
                try:
                    res = app.pipeline.select(d.get("run_id"), d.get("variant_id"))
                    return self._json(200, res)
                except Exception as exc:
                    return self._json(400, {"error": str(exc)})
            return self._json(404, {"error": "not found"})

        # ---- static ----
        def _static(self, p):
            dist = app.dist_dir()
            if dist is not None:
                rel = os.path.normpath(p.lstrip("/")) if p not in ("", "/") else "index.html"
                full = os.path.join(dist, rel)
                if not os.path.abspath(full).startswith(os.path.abspath(dist) + os.sep) \
                        and os.path.abspath(full) != os.path.abspath(dist):
                    return self._send(403, b"forbidden")
                if os.path.isfile(full):
                    return self._file(full, cache=rel.startswith("assets" + os.sep))
                # SPA fallback: unknown non-API routes render the app shell
                return self._file(os.path.join(dist, "index.html"), "text/html")
            # legacy console
            legacy = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
            if p in legacy:
                return self._file(os.path.join(WEB_LEGACY, legacy[p]))
            return self._send(404, b"not found")

        def _serve_run(self, p):
            rel = p[len("/runs/"):]
            full = os.path.normpath(os.path.join(app.cfg.work_dir, rel))
            if not full.startswith(os.path.abspath(app.cfg.work_dir)):
                return self._send(403, b"forbidden")
            self._file(full, "text/html")

        # ---- SSE ----
        def _sse(self, broker):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                for e in broker.stream():
                    if e is None:
                        self.wfile.write(b": keepalive\n\n")
                    else:
                        self.wfile.write(
                            f"data: {json.dumps(e, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def _legacy_stream(self):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            run_id = (q.get("run_id") or [""])[0]
            broker = app.brokers.get(run_id)
            if not broker:
                return self._send(404, b"no such run")
            return self._sse(broker)
    return H


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """A browser closing an SSE tab is not an error worth a traceback."""
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def serve(cfg, app=None):
    """Bind and return (httpd, app) without blocking — used by tests and main()."""
    os.makedirs(cfg.work_dir, exist_ok=True)
    app = app or App(cfg)
    httpd = _Server((cfg.host, cfg.port), _handler(app))
    return httpd, app


def main():
    cfg = Config()
    httpd, app = serve(cfg)
    labels = app.health()["providers"]
    print(f"\n  ✦ Airlock — selection pressure, not agent spam")
    print(f"    running at  {app.base_url}")
    print(f"    runner      {labels['runner']}   sandbox={labels['sandbox']}")
    print(f"    frontend    {'web/dist' if app.dist_dir() else 'web-legacy (run `npm run build` in web/)'}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  bye.")


if __name__ == "__main__":
    main()

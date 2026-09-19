"""HTTP API + runner contract tests. Offline, deterministic, no Daytona.

    cd airlock && python3 -m unittest tests.research.test_api -v

The server is spun up on a free port in a thread with a FAKE research loop
injected through `App(loop_factory=...)`, so nothing here depends on loop.py,
catalog.py or evaluate.py being finished.
"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from airlock.config import Config                    # noqa: E402
from airlock import server as srv                    # noqa: E402
from airlock.research import runners                 # noqa: E402


# ------------------------------------------------------------ fake loop --
CANNED = [
    {"type": "lab_started", "task": "breast_cancer", "metric": "cv_accuracy",
     "catalog_summary": {"factors": 31, "families": 4}, "providers": {"runner": "local"},
     "budget": 40},
    {"type": "agent_message", "text": "Starting on `breast_cancer` · metric: 5-fold accuracy."},
    {"type": "hypothesis", "id": "h1", "text": "standardize helps logistic", "kind": "factor",
     "family": "transform", "control": "logistic", "treatment": "standardize+logistic",
     "cost_est": 2, "why": "scale-sensitive model"},
    {"type": "experiment_started", "id": "e1", "hypothesis_id": "h1", "lane": "confirm",
     "spec": {"transform": "standardize", "model": "logistic"}, "runner": "local"},
    {"type": "experiment_result", "id": "e1", "metric": 0.951, "std": 0.01,
     "delta_vs_control": 0.004, "cost_ms": 120, "gate": "keep", "reason": "beats bar"},
    {"type": "ask", "id": "ask1", "question": "Run 6 stacking recipes?",
     "options": [{"id": "run", "label": "Run"}, {"id": "skip", "label": "Skip"}],
     "reason": "~40s"},
]
AFTER_ANSWER = [
    {"type": "champion", "spec": {"ensemble": "stack"}, "metric": 0.972,
     "delta_vs_best_single": 0.014, "evals_used": 23, "evals_grid_equivalent": 180},
    {"type": "done", "summary": "champion found in 23 evals"},
]


class FakeLoop:
    """Emits a canned sequence, pauses on the `ask` until answered, and records
    steering. Mirrors the interface server.py drives: run() / steer() / answer()."""
    instances = []

    def __init__(self, cfg, lab_id, prompt, task, budget, seed, emit):
        self.lab_id, self.prompt, self.seed, self.emit = lab_id, prompt, seed, emit
        self.steers, self.answers = [], []
        self.answered = threading.Event()
        FakeLoop.instances.append(self)

    def run(self):
        for e in CANNED:
            self.emit(dict(e))
        self.answered.wait(timeout=10)
        for e in AFTER_ANSWER:
            self.emit(dict(e))

    def steer(self, text):
        self.steers.append(text)
        self.emit({"type": "agent_message", "text": f"Noted: {text}"})
        return {"queued": len(self.steers)}

    def answer(self, ask_id, option_id):
        self.answers.append((ask_id, option_id))
        self.answered.set()
        return {"accepted": True}


def fake_factory(cfg, lab_id, prompt, task, budget, seed, emit):
    return FakeLoop(cfg, lab_id, prompt, task, budget, seed, emit)


def broken_factory(*a, **k):
    raise ImportError("loop.py is mid-development")


# ------------------------------------------------------------ http utils --
class Client:
    def __init__(self, port):
        self.port = port

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        c.request(method, path, body=data,
                  headers={"Content-Type": "application/json"} if data else {})
        r = c.getresponse()
        raw = r.read()
        c.close()
        try:
            return r.status, json.loads(raw), r.getheader("Content-Type")
        except ValueError:
            return r.status, raw, r.getheader("Content-Type")

    def sse(self, path, until_type="done", timeout=10):
        """Read SSE frames until an event of `until_type` (or the stream closes)."""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        c.request("GET", path)
        r = c.getresponse()
        assert r.status == 200, r.status
        assert r.getheader("Content-Type").startswith("text/event-stream")
        events = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = r.fp.readline()
            if not line:
                break
            if line.startswith(b"data: "):
                e = json.loads(line[6:])
                events.append(e)
                if e.get("type") == until_type:
                    break
        c.close()
        return events


def start_server(cfg, factory):
    app = srv.App(cfg, loop_factory=factory)
    httpd, app = srv.serve(cfg, app)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, app, httpd.server_address[1]


# ------------------------------------------------------------------ tests --
class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="airlock-api-")
        cfg = Config()
        cfg.host, cfg.port = "127.0.0.1", 0
        cfg.daytona_key = ""                                   # offline
        cfg.work_dir = os.path.join(cls.tmp, "runs")
        cls.httpd, cls.app, port = start_server(cfg, fake_factory)
        cls.c = Client(port)
        # a temp dist for static serving
        cls.dist = os.path.join(cls.tmp, "dist")
        os.makedirs(os.path.join(cls.dist, "assets"))
        with open(os.path.join(cls.dist, "index.html"), "w") as f:
            f.write("<!doctype html><title>Airlock</title><div id=root></div>")
        with open(os.path.join(cls.dist, "assets", "app.js"), "w") as f:
            f.write("console.log('airlock')")
        cls.app._dist_override = cls.dist

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_health(self):
        st, body, _ = self.c.req("GET", "/api/health")
        self.assertEqual(st, 200)
        self.assertTrue(body["ok"])
        self.assertIn("runner", body["providers"])
        self.assertIn(body["providers"]["runner"], ("local", "daytona"))
        self.assertEqual(body["providers"]["sandbox"], "local-render")
        self.assertEqual(body["config"]["port"], 0)

    def test_lab_lifecycle_stream_steer_answer(self):
        st, body, _ = self.c.req("POST", "/api/labs", {"prompt": "best composite for breast_cancer",
                                                        "budget": 40, "seed": 7})
        self.assertEqual(st, 200)
        lab_id = body["lab_id"]
        self.assertTrue(lab_id)

        # replay-from-0: the first frames are the canned prefix, in order, up to the ask
        head = self.c.sse(f"/api/labs/{lab_id}/stream", until_type="ask")
        self.assertEqual([e["type"] for e in head], [e["type"] for e in CANNED])
        self.assertEqual([e["seq"] for e in head], list(range(len(CANNED))))
        self.assertTrue(all("ts" in e for e in head))
        self.assertEqual(head[0]["task"], "breast_cancer")

        loop = next(l for l in FakeLoop.instances if l.lab_id == lab_id)
        self.assertEqual(loop.seed, 7)

        # while paused on the ask, the sidebar says "waiting"
        st, labs, _ = self.c.req("GET", "/api/labs")
        me = next(l for l in labs if l["id"] == lab_id)
        self.assertEqual(me["status"], "waiting")
        self.assertEqual(me["title"], "breast_cancer")
        self.assertEqual(me["last_message"], "Run 6 stacking recipes?")

        # steer round-trip: ack returned, loop saw it, chat got the user + agent lines
        st, ack, _ = self.c.req("POST", f"/api/labs/{lab_id}/steer", {"text": "focus family ensemble"})
        self.assertEqual(st, 200)
        self.assertTrue(ack["ok"])
        self.assertEqual(ack["queued"], 1)
        self.assertEqual(loop.steers, ["focus family ensemble"])

        st, _, _ = self.c.req("POST", f"/api/labs/{lab_id}/steer", {"text": ""})
        self.assertEqual(st, 400)

        # answer round-trip releases the loop
        st, ack, _ = self.c.req("POST", f"/api/labs/{lab_id}/answer",
                                {"ask_id": "ask1", "option_id": "run"})
        self.assertEqual(st, 200)
        self.assertTrue(ack["accepted"])
        self.assertEqual(loop.answers, [("ask1", "run")])

        # a late subscriber replays everything from 0 through done, in order
        full = self.c.sse(f"/api/labs/{lab_id}/stream", until_type="done")
        types = [e["type"] for e in full]
        expected = ([e["type"] for e in CANNED] + ["user_message", "agent_message", "answered"]
                    + [e["type"] for e in AFTER_ANSWER])
        self.assertEqual(types, expected)
        self.assertEqual([e["seq"] for e in full], list(range(len(full))))
        self.assertEqual(full[types.index("user_message")]["text"], "focus family ensemble")
        self.assertEqual(full[types.index("answered")]["option_id"], "run")

        st, labs, _ = self.c.req("GET", "/api/labs")
        me = next(l for l in labs if l["id"] == lab_id)
        self.assertEqual(me["status"], "done")
        self.assertEqual(me["last_message"], "champion found in 23 evals")
        self.assertEqual(me["events"], len(full))

    def test_listing_sorted_newest_first(self):
        ids = []
        for i in range(2):
            st, body, _ = self.c.req("POST", "/api/labs", {"prompt": f"lab {i}"})
            ids.append(body["lab_id"])
            time.sleep(0.02)
        st, labs, _ = self.c.req("GET", "/api/labs")
        order = [l["id"] for l in labs]
        self.assertLess(order.index(ids[1]), order.index(ids[0]))
        for l in labs:
            for k in ("id", "title", "last_message", "updated", "status"):
                self.assertIn(k, l)
        for i in ids:                                   # release the fakes
            self.c.req("POST", f"/api/labs/{i}/answer", {"ask_id": "ask1", "option_id": "skip"})

    def test_unknown_lab(self):
        st, body, _ = self.c.req("GET", "/api/labs/nope/stream")
        self.assertEqual(st, 404)
        st, body, _ = self.c.req("POST", "/api/labs/nope/steer", {"text": "x"})
        self.assertEqual(st, 404)
        st, body, _ = self.c.req("POST", "/api/labs/nope/answer", {"ask_id": "a", "option_id": "b"})
        self.assertEqual(st, 404)
        st, body, _ = self.c.req("GET", "/api/nothing")
        self.assertEqual(st, 404)

    def test_static_dist_and_spa_fallback(self):
        st, body, ctype = self.c.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn(b"Airlock", body)
        self.assertTrue(ctype.startswith("text/html"))
        st, body, ctype = self.c.req("GET", "/assets/app.js")
        self.assertEqual(st, 200)
        self.assertIn(b"airlock", body)
        self.assertTrue(ctype.startswith(("text/javascript", "application/javascript")))
        st, body, ctype = self.c.req("GET", "/labs/abc123")        # SPA route
        self.assertEqual(st, 200)
        self.assertIn(b"<div id=root>", body)
        st, body, _ = self.c.req("GET", "/../../etc/passwd")
        self.assertIn(st, (200, 403, 404))                          # never leaks: normalised into dist
        self.assertNotIn(b"root:", body if isinstance(body, bytes) else b"")

    def test_legacy_routes_still_answer(self):
        st, body, _ = self.c.req("GET", "/api/stream?run_id=missing")
        self.assertEqual(st, 404)
        st, body, _ = self.c.req("GET", "/runs/../../../etc/passwd")
        self.assertIn(st, (403, 404))
        st, body, _ = self.c.req("POST", "/api/select", {"run_id": "x", "variant_id": "y"})
        self.assertEqual(st, 400)


class LoopUnavailableTests(unittest.TestCase):
    def test_503_when_loop_import_fails(self):
        tmp = tempfile.mkdtemp(prefix="airlock-503-")
        cfg = Config()
        cfg.host, cfg.port, cfg.daytona_key = "127.0.0.1", 0, ""
        cfg.work_dir = os.path.join(tmp, "runs")
        httpd, app, port = start_server(cfg, broken_factory)
        try:
            st, body, _ = Client(port).req("POST", "/api/labs", {"prompt": "x"})
            self.assertEqual(st, 503)
            self.assertIn("mid-development", body["error"])
            st, labs, _ = Client(port).req("GET", "/api/labs")
            self.assertEqual(labs, [])
        finally:
            httpd.shutdown(); httpd.server_close(); shutil.rmtree(tmp, ignore_errors=True)


class BrokerTests(unittest.TestCase):
    def test_replay_then_live_then_close(self):
        b = srv.Broker()
        b.KEEPALIVE_S = 0.05
        b.emit({"type": "a"})
        got = []
        gen = b.stream()
        got.append(next(gen))
        self.assertIsNone(next(gen))                    # keepalive tick while idle
        b.emit({"type": "done"})
        got.append(next(gen))
        self.assertEqual([g["type"] for g in got], ["a", "done"])
        self.assertRaises(StopIteration, next, gen)


# ------------------------------------------------------------ runners --
def stub_eval(recipe, seed):
    """Deterministic, importable by name → exercises the process pool."""
    if recipe.get("model") == "boom":
        raise ValueError("bad recipe")
    return {"metric": 0.9 + seed / 1000 + len(recipe.get("transform", "")) / 100,
            "std": 0.01, "params": 12, "cost_ms": 3}


SPECS = [
    {"spec_id": "s1", "recipe": {"transform": "none", "model": "logistic"}, "seed": 1},
    {"spec_id": "s2", "recipe": {"transform": "standardize", "model": "knn"}, "seed": 2},
    {"spec_id": "s3", "recipe": {"transform": "pca", "model": "boom"}, "seed": 3},
]


def check_results(tc, results, sandbox):
    tc.assertEqual([r["spec_id"] for r in results], ["s1", "s2", "s3"])
    tc.assertAlmostEqual(results[0]["metric"], 0.9 + 0.001 + 0.04)
    tc.assertAlmostEqual(results[1]["metric"], 0.9 + 0.002 + 0.11)
    tc.assertIsNone(results[2]["metric"])
    tc.assertIn("bad recipe", results[2]["error"])
    for r in results:
        tc.assertEqual(r["runtime"]["sandbox"], sandbox)
        tc.assertIsInstance(r["runtime"]["ms"], int)
        tc.assertIsInstance(r["cost_ms"], int)
        for k in ("spec_id", "metric", "std", "cost_ms", "params", "runtime", "error"):
            tc.assertIn(k, r)


class LocalRunnerTests(unittest.TestCase):
    def test_thread_mode_with_closure(self):
        seen = []
        r = runners.LocalRunner(evaluator=lambda rec, seed: stub_eval(rec, seed), workers=2)
        self.assertEqual(r.mode, "thread")
        out = r.evaluate_many(SPECS, on_result=seen.append)
        check_results(self, out, "local")
        self.assertEqual(sorted(s["spec_id"] for s in seen), ["s1", "s2", "s3"])
        r.teardown()

    def test_process_mode_with_importable_stub(self):
        seen = []
        r = runners.LocalRunner(evaluator=stub_eval, workers=2)
        self.assertEqual(r.mode, "process")
        self.assertEqual(r.label, "local")
        out = r.evaluate_many(SPECS, on_result=seen.append)
        check_results(self, out, "local")
        self.assertEqual(len(seen), 3)
        out2 = r.evaluate_many(SPECS)                   # pool is reused
        self.assertEqual(out2[0]["metric"], out[0]["metric"])
        r.teardown()

    def test_default_worker_count(self):
        r = runners.LocalRunner(evaluator=stub_eval)
        self.assertEqual(r.workers, max(1, os.cpu_count() - 2))
        self.assertEqual(r.evaluate_many([]), [])


class FakeSandbox:
    """Stands in for a Daytona sandbox: stores uploads, runs the driver in-process."""
    def __init__(self, sid, evaluator, fail_exec=False):
        self.id, self.evaluator, self.fail_exec = sid, evaluator, fail_exec
        self.files, self.cmds, self.deleted = {}, [], False
        self.fs, self.process = self, self

    def upload_file(self, data, dst):
        self.files[dst] = data

    def exec(self, cmd, cwd=None, env=None, timeout=None):
        self.cmds.append(cmd)
        if self.fail_exec:
            raise ConnectionError("sandbox gone")
        if "pip install" in cmd:
            return type("R", (), {"exit_code": 0, "result": ""})()
        time.sleep(0.02)                 # a real exec takes time: lets every pool worker pull
        spec_file = cmd.split()[-1]
        spec = json.loads(self.files[f"{runners.REMOTE_DIR}/{spec_file}"])
        try:
            out = dict(self.evaluator(spec["recipe"], spec["seed"]))
            out["error"] = None
        except Exception as exc:
            out = {"metric": None, "error": f"{type(exc).__name__}: {exc}"}
        return type("R", (), {"exit_code": 0,
                              "result": "noise\n" + runners.MARK + json.dumps(out) + "\n"})()

    def delete(self):
        self.deleted = True


class FakeClient:
    def __init__(self, evaluator, image_ok=True, fail_ids=()):
        self.evaluator, self.image_ok, self.fail_ids = evaluator, image_ok, set(fail_ids)
        self.created = []

    def create(self, params=None, timeout=None):
        if params is not None and not self.image_ok:
            raise RuntimeError("image build unsupported")
        sid = f"sb-{len(self.created)}"
        sb = FakeSandbox(sid, self.evaluator, fail_exec=sid in self.fail_ids)
        self.created.append(sb)
        return sb


class DaytonaRunnerTests(unittest.TestCase):
    def test_pool_provisions_once_and_streams(self):
        client = FakeClient(stub_eval)
        r = runners.DaytonaRunner(cfg=None, evaluator=stub_eval, pool_size=2, client=client)
        self.assertEqual(r.label, "daytona")
        seen = []
        out = r.evaluate_many(SPECS, on_result=seen.append)
        check_results(self, out, "daytona")
        self.assertEqual(len(client.created), 2)
        self.assertEqual(set(r.sandbox_ids()), {"sb-0", "sb-1"})
        self.assertTrue(all(o["runtime"]["sandbox_id"] in ("sb-0", "sb-1") for o in out))
        self.assertEqual(len(seen), 3)
        # bundle uploaded once per sandbox: driver + package files
        for sb in client.created:
            self.assertIn(f"{runners.REMOTE_DIR}/{runners.DRIVER_NAME}", sb.files)
            self.assertIn(f"{runners.REMOTE_DIR}/airlock/research/__init__.py", sb.files)
        r.evaluate_many(SPECS)                          # no re-provision
        self.assertEqual(len(client.created), 2)
        r.teardown()
        self.assertTrue(all(sb.deleted for sb in client.created))
        self.assertEqual(r.sandboxes, [])

    def test_image_fallback_to_plain_create_and_pip(self):
        client = FakeClient(stub_eval, image_ok=False)
        r = runners.DaytonaRunner(cfg=None, pool_size=1, client=client)
        r.provision()
        self.assertEqual(len(client.created), 1)
        self.assertTrue(any("pip install" in c for c in client.created[0].cmds))
        r.teardown()

    def test_dead_sandbox_retires_and_another_takes_over(self):
        client = FakeClient(stub_eval, fail_ids={"sb-0"})
        r = runners.DaytonaRunner(cfg=None, pool_size=2, client=client)
        out = r.evaluate_many(SPECS)
        check_results(self, out, "daytona")
        self.assertTrue(all(o["runtime"]["sandbox_id"] == "sb-1" for o in out))
        self.assertEqual(r.sandbox_ids(), ["sb-1"])        # the dead one retired
        out2 = r.evaluate_many(SPECS)                        # still on Daytona
        check_results(self, out2, "daytona")
        r.teardown()

    def test_all_sandboxes_dying_finishes_locally(self):
        client = FakeClient(stub_eval, fail_ids={"sb-0", "sb-1"})
        r = runners.DaytonaRunner(cfg=None, evaluator=stub_eval, pool_size=2, client=client)
        seen = []
        out = r.evaluate_many(SPECS, on_result=seen.append)
        check_results(self, out, "local")
        self.assertEqual(len(seen), 3)
        self.assertEqual(r.sandbox_ids(), [])
        r.teardown()

    def test_degrades_to_local_when_nothing_boots(self):
        class DeadClient:
            def create(self, *a, **k):
                raise RuntimeError("quota")
        r = runners.DaytonaRunner(cfg=None, evaluator=stub_eval, pool_size=2, client=DeadClient())
        out = r.evaluate_many(SPECS)
        check_results(self, out, "local")
        r.teardown()

    def test_driver_script_runs_standalone(self):
        """The uploaded driver really is self-contained python: run it here with a
        stub `airlock.research.evaluate` on its path."""
        import subprocess
        tmp = tempfile.mkdtemp(prefix="airlock-drv-")
        try:
            files = runners._bundle_files()
            for path, data in files.items():
                full = os.path.join(tmp, path)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "wb") as f:
                    f.write(data)
            ws = os.path.join(tmp, runners.REMOTE_DIR)
            with open(os.path.join(ws, "airlock", "research", "evaluate.py"), "w") as f:
                f.write("def evaluate(recipe, seed):\n"
                        "    return {'metric': 0.5 + seed, 'std': 0.0, 'params': 1, 'cost_ms': 1}\n")
            with open(os.path.join(ws, "spec_x.json"), "w") as f:
                json.dump({"recipe": {"model": "m"}, "seed": 2}, f)
            p = subprocess.run([sys.executable, runners.DRIVER_NAME, "spec_x.json"],
                               cwd=ws, capture_output=True, text=True, timeout=30)
            line = next(l for l in p.stdout.splitlines() if l.startswith(runners.MARK))
            out = json.loads(line[len(runners.MARK):])
            self.assertEqual(out["metric"], 2.5)
            self.assertIsNone(out["error"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class FactoryTests(unittest.TestCase):
    def test_get_runner_local_without_key(self):
        cfg = Config(); cfg.daytona_key = ""
        r = runners.get_runner(cfg, evaluator=stub_eval)
        self.assertIsInstance(r, runners.LocalRunner)
        self.assertEqual(runners.runner_label(cfg), "local")

    def test_forced_local(self):
        cfg = Config(); cfg.daytona_key = "dtn_fake"
        old = os.environ.get("AIRLOCK_RUNNER")
        os.environ["AIRLOCK_RUNNER"] = "local"
        try:
            self.assertIsInstance(runners.get_runner(cfg, evaluator=stub_eval), runners.LocalRunner)
            self.assertEqual(runners.runner_label(cfg), "local")
        finally:
            if old is None:
                os.environ.pop("AIRLOCK_RUNNER", None)
            else:
                os.environ["AIRLOCK_RUNNER"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)

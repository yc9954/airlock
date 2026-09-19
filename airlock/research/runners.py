"""Runners: where a recipe gets evaluated.

Two implementations behind one interface, so the research loop never knows or
cares whether an experiment ran on this machine or in a Daytona sandbox:

    class Runner:
        label: str                                   # 'local' | 'daytona'
        def evaluate_many(self, specs, on_result=None) -> list[dict]
        def teardown(self)

    specs   : [{'spec_id': str, 'recipe': {...}, 'seed': int}, ...]
    results : [{'spec_id', 'metric': float|None, 'std', 'cost_ms', 'params',
                'runtime': {'sandbox': 'local'|'daytona', 'sandbox_id', 'ms'},
                'error': str|None}, ...]           # same order as `specs`
    on_result(result) fires as each finishes, in completion order (live streaming).

The pure evaluator is `airlock.research.evaluate.evaluate(recipe, seed)`; both
runners take an injected `evaluator` so they are testable with a stub.

* `LocalRunner`  — a multiprocessing pool of `cpu_count - 2` workers (spawn
  context, so it is safe to drive from a server thread). Evaluators that cannot
  be pickled by reference (lambdas, closures) fall back to a thread pool.
* `DaytonaRunner` — a POOL of N sandboxes provisioned once from an image with
  scikit-learn, each pulling specs from a shared queue and running a small
  self-contained driver (`python3 airlock_eval.py spec.json`). Per-candidate
  cost is seconds, not a boot. Degrades to `LocalRunner` with a logged reason
  when the SDK, the key, or the network is missing.

`get_runner(cfg)` picks Daytona when `DAYTONA_API_KEY` is set (`.env` is honored
by `airlock.config`), unless `AIRLOCK_RUNNER=local` forces the local pool.
"""
import importlib
import json
import multiprocessing
import os
import queue
import sys
import threading
import time
import traceback
from multiprocessing.pool import ThreadPool

DEFAULT_EVALUATOR = ("airlock.research.evaluate", "evaluate")
HERE = os.path.dirname(os.path.abspath(__file__))


def _log(msg):
    print(f"  [runner] {msg}", file=sys.stderr, flush=True)


def _resolve(ref):
    """(module, qualname) -> callable, importing lazily."""
    mod = importlib.import_module(ref[0])
    fn = mod
    for part in ref[1].split("."):
        fn = getattr(fn, part)
    return fn


def _ref_of(fn):
    """A picklable (module, qualname) reference for `fn`, or None if it cannot be
    re-imported by name in a fresh process (lambda, closure, local def)."""
    mod, qn = getattr(fn, "__module__", None), getattr(fn, "__qualname__", None)
    if not mod or not qn or "<" in qn or mod == "__main__":
        return None
    try:
        return (mod, qn) if _resolve((mod, qn)) is fn else None
    except Exception:
        return None


def _shape(spec_id, out, ms, sandbox, sandbox_id=None, error=None):
    """Normalise whatever an evaluator returned into the result contract."""
    out = out or {}
    metric = out.get("metric")
    return {
        "spec_id": spec_id,
        "metric": float(metric) if metric is not None and error is None else None,
        "std": float(out.get("std") or 0.0) if error is None else None,
        "cost_ms": int(out.get("cost_ms") if out.get("cost_ms") is not None else ms),
        "params": out.get("params"),
        "runtime": {"sandbox": sandbox, "sandbox_id": sandbox_id, "ms": int(ms)},
        "error": error,
    }


# ---------------------------------------------------------------- local pool --
_WORKER_EVAL = None


def _worker_init(ref):
    global _WORKER_EVAL
    _WORKER_EVAL = _resolve(ref)


def _worker_run(job):
    """Runs inside a pool worker. Never raises: an exception becomes a result
    with `error` set and `metric=None`, so one bad recipe cannot kill a batch."""
    idx, spec, evaluator = job
    fn = evaluator or _WORKER_EVAL
    t0 = time.time()
    try:
        out = fn(spec["recipe"], int(spec.get("seed", 0)))
        return idx, _shape(spec["spec_id"], out, (time.time() - t0) * 1000, "local")
    except Exception as exc:
        return idx, _shape(spec["spec_id"], None, (time.time() - t0) * 1000, "local",
                           error=f"{type(exc).__name__}: {exc}")


class Runner:
    label = "abstract"

    def evaluate_many(self, specs, on_result=None):
        raise NotImplementedError

    def teardown(self):
        pass


class LocalRunner(Runner):
    label = "local"

    def __init__(self, evaluator=None, workers=None):
        self._evaluator = evaluator
        self._ref = DEFAULT_EVALUATOR if evaluator is None else _ref_of(evaluator)
        n = workers if workers else max(1, (os.cpu_count() or 2) - 2)
        self.workers = max(1, int(n))
        self.mode = "process" if self._ref else "thread"
        self._pool = None
        self._lock = threading.Lock()

    def _get_pool(self):
        with self._lock:
            if self._pool is None:
                if self.mode == "process":
                    ctx = multiprocessing.get_context("spawn")
                    self._pool = ctx.Pool(self.workers, initializer=_worker_init,
                                          initargs=(self._ref,))
                else:
                    self._pool = ThreadPool(self.workers)
            return self._pool

    def evaluate_many(self, specs, on_result=None):
        specs = list(specs)
        if not specs:
            return []
        results = [None] * len(specs)
        # In process mode the evaluator is resolved inside the worker (by ref);
        # in thread mode the callable itself travels with the job.
        payload = None if self.mode == "process" else self._evaluator
        jobs = [(i, s, payload) for i, s in enumerate(specs)]
        try:
            pool = self._get_pool()
            for idx, res in pool.imap_unordered(_worker_run, jobs):
                results[idx] = res
                if on_result:
                    on_result(res)
        except Exception as exc:                     # a worker died, pool broken
            _log(f"local pool failed ({exc}); marking remaining specs as crashed")
            self.teardown()
            for i, s in enumerate(specs):
                if results[i] is None:
                    results[i] = _shape(s["spec_id"], None, 0, "local",
                                        error=f"pool failure: {exc}")
                    if on_result:
                        on_result(results[i])
        return results

    def teardown(self):
        with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            try:
                pool.terminate()
                pool.join()
            except Exception:
                pass


# ------------------------------------------------------------- daytona pool --
REMOTE_DIR = "airlock_ws"
DRIVER_NAME = "airlock_eval.py"
MARK = "__AIRLOCK_RESULT__"

# The self-contained driver uploaded to every sandbox once. It imports the same
# evaluate.py the local runner uses (uploaded alongside), so a Daytona result and
# a local result for the same (recipe, seed) are byte-for-byte comparable.
DRIVER = r'''
import json, os, sys, time, traceback
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
MARK = "__AIRLOCK_RESULT__"
def main():
    spec = json.load(open(sys.argv[1]))
    t0 = time.time()
    try:
        from airlock.research.evaluate import evaluate
        out = evaluate(spec["recipe"], int(spec.get("seed", 0))) or {}
        out = {"metric": out.get("metric"), "std": out.get("std"),
               "params": out.get("params"), "cost_ms": out.get("cost_ms"),
               "ms": round((time.time() - t0) * 1000), "error": None}
    except Exception as exc:
        out = {"metric": None, "std": None, "params": None, "cost_ms": None,
               "ms": round((time.time() - t0) * 1000),
               "error": type(exc).__name__ + ": " + str(exc),
               "trace": traceback.format_exc()[-2000:]}
    print(MARK + json.dumps(out), flush=True)
main()
'''


def _bundle_files():
    """The files a sandbox needs: airlock/__init__.py + airlock/research/*.py
    (minus this module and the loop, which are host-side) + the driver."""
    files = {f"{REMOTE_DIR}/{DRIVER_NAME}": DRIVER.encode()}
    pkg_root = os.path.dirname(HERE)                       # airlock/
    init = os.path.join(pkg_root, "__init__.py")
    files[f"{REMOTE_DIR}/airlock/__init__.py"] = b""
    if os.path.exists(init):
        with open(init, "rb") as f:
            files[f"{REMOTE_DIR}/airlock/__init__.py"] = f.read()
    files[f"{REMOTE_DIR}/airlock/research/__init__.py"] = b""
    for name in sorted(os.listdir(HERE)):
        if not name.endswith(".py") or name in ("runners.py", "loop.py", "__init__.py"):
            continue
        with open(os.path.join(HERE, name), "rb") as f:
            files[f"{REMOTE_DIR}/airlock/research/{name}"] = f.read()
    return files


class DaytonaRunner(Runner):
    """A pool of N sandboxes, provisioned once, each a worker pulling from a queue."""
    label = "daytona"
    PROVISION_TIMEOUT = 180
    EXEC_TIMEOUT = 300

    def __init__(self, cfg=None, evaluator=None, pool_size=None, client=None, eager=False):
        self.cfg = cfg
        self._evaluator = evaluator              # used only by the local fallback
        self.pool_size = max(1, int(pool_size or os.environ.get("AIRLOCK_DAYTONA_POOL", 4)))
        self.sandboxes = []                      # provisioned handles
        self.boot_ms = {}                        # sandbox_id -> boot time
        self._fallback = None
        self._lock = threading.Lock()
        self._provisioned = False
        if client is not None:
            self.client = client
        else:
            from daytona import Daytona, DaytonaConfig          # raises if SDK missing
            key = getattr(cfg, "daytona_key", "") or os.environ.get("DAYTONA_API_KEY", "")
            if not key:
                raise RuntimeError("DAYTONA_API_KEY not set")
            self.client = Daytona(DaytonaConfig(api_key=key))
        if eager:
            self.provision()

    # ---- provisioning ----
    def _create_one(self):
        t0 = time.time()
        sb = None
        try:
            from daytona import CreateSandboxFromImageParams, Image
            params = CreateSandboxFromImageParams(
                image=Image.debian_slim("3.12").pip_install("scikit-learn", "numpy"))
        except Exception as exc:                 # SDK missing: fake/injected client
            _log(f"image API unavailable ({exc}); plain create + pip install")
            params = None
        try:
            if params is None:
                raise RuntimeError("no image params")
            sb = self.client.create(params, timeout=self.PROVISION_TIMEOUT)
        except Exception as exc:
            if params is not None:
                _log(f"image create failed ({exc}); plain create + pip install")
            sb = self.client.create()
            r = sb.process.exec("python3 -m pip install -q scikit-learn numpy "
                                "|| pip install -q scikit-learn numpy", timeout=self.EXEC_TIMEOUT)
            if getattr(r, "exit_code", 0) not in (0, None):
                raise RuntimeError(f"pip install failed: {getattr(r, 'result', '')[-300:]}")
        for path, data in _bundle_files().items():
            sb.fs.upload_file(data, path)
        self.boot_ms[getattr(sb, "id", None)] = round((time.time() - t0) * 1000)
        return sb

    def provision(self):
        """Boot the pool once (in parallel). Returns the number of live sandboxes."""
        with self._lock:
            if self._provisioned:
                return len(self.sandboxes)
            self._provisioned = True
            t0 = time.time()
            with ThreadPool(self.pool_size) as tp:
                for sb in tp.imap_unordered(lambda _: self._try_create(), range(self.pool_size)):
                    if sb is not None:
                        self.sandboxes.append(sb)
            _log(f"daytona pool: {len(self.sandboxes)}/{self.pool_size} sandboxes "
                 f"up in {round((time.time() - t0) * 1000)} ms")
            return len(self.sandboxes)

    def _try_create(self):
        try:
            return self._create_one()
        except Exception as exc:
            _log(f"sandbox provision failed: {exc}")
            return None

    def sandbox_ids(self):
        return [getattr(sb, "id", None) for sb in self.sandboxes]

    # ---- evaluation ----
    def _run_one(self, sb, spec):
        sid = getattr(sb, "id", None)
        t0 = time.time()
        # spec ids look like "standardize|logistic" — `|` would become a shell pipe in exec.
        fname = "spec_" + "".join(ch if ch.isalnum() else "_" for ch in spec["spec_id"]) + ".json"
        path = f"{REMOTE_DIR}/{fname}"
        sb.fs.upload_file(json.dumps({"recipe": spec["recipe"],
                                      "seed": int(spec.get("seed", 0))}).encode(), path)
        r = sb.process.exec(f"cd {REMOTE_DIR} && python3 {DRIVER_NAME} {fname}",
                            timeout=self.EXEC_TIMEOUT)
        text = getattr(r, "result", "") or ""
        ms = (time.time() - t0) * 1000
        line = next((ln for ln in text.splitlines() if ln.startswith(MARK)), None)
        if line is None:
            tail = text.strip()[-400:]
            return _shape(spec["spec_id"], None, ms, "daytona", sid,
                          error=f"driver produced no result (exit {getattr(r, 'exit_code', '?')}): {tail}")
        out = json.loads(line[len(MARK):])
        return _shape(spec["spec_id"], out, ms, "daytona", sid, error=out.get("error"))

    def evaluate_many(self, specs, on_result=None):
        specs = list(specs)
        if not specs:
            return []
        self.provision()                           # no-op after the first call
        if not self.sandboxes:                     # nothing booted, or all retired
            if self._fallback is None:
                _log("no Daytona sandbox available; degrading to the local pool")
                self._fallback = LocalRunner(self._evaluator)
            return self._fallback.evaluate_many(specs, on_result)

        results = [None] * len(specs)
        q = queue.Queue()
        for i, s in enumerate(specs):
            q.put((i, s, 0))
        emit_lock = threading.Lock()

        def worker(sb):
            while True:
                try:
                    i, s, tries = q.get_nowait()
                except queue.Empty:
                    return
                try:
                    res = self._run_one(sb, s)
                except Exception as exc:           # transport-level failure: this
                    sid = getattr(sb, "id", None)  # sandbox retires, the spec is requeued
                    _log(f"sandbox {sid} failed ({exc}); retiring it from the pool")
                    with self._lock:
                        if sb in self.sandboxes:
                            self.sandboxes.remove(sb)
                    q.put((i, s, tries + 1))
                    return
                results[i] = res
                if on_result:
                    with emit_lock:
                        on_result(res)

        threads = [threading.Thread(target=worker, args=(sb,), daemon=True)
                   for sb in list(self.sandboxes)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        left = [(i, s) for i, s in enumerate(specs) if results[i] is None]
        if left:                                   # every sandbox retired mid-queue
            _log(f"{len(left)} spec(s) unresolved after the pool retired; "
                 f"finishing them on the local pool")
            if self._fallback is None:
                self._fallback = LocalRunner(self._evaluator)
            for (i, _), res in zip(left, self._fallback.evaluate_many([s for _, s in left])):
                results[i] = res
                if on_result:
                    on_result(res)
        return results

    def teardown(self):
        with self._lock:
            sbs, self.sandboxes = self.sandboxes, []
        for sb in sbs:
            try:
                (getattr(sb, "delete", None) or (lambda: self.client.delete(sb)))()
            except Exception as exc:
                _log(f"delete failed for {getattr(sb, 'id', '?')}: {exc}")
        if self._fallback is not None:
            self._fallback.teardown()


# ------------------------------------------------------------------ factory --
def _sdk_available():
    try:
        importlib.import_module("daytona")
        return True
    except Exception:
        return False


def runner_label(cfg=None):
    """What `get_runner` would pick, without provisioning anything (for /api/health)."""
    forced = os.environ.get("AIRLOCK_RUNNER", "").lower()
    if forced == "local":
        return "local"
    key = getattr(cfg, "daytona_key", None)
    if key is None:
        key = os.environ.get("DAYTONA_API_KEY", "")
    return "daytona" if key and _sdk_available() else "local"


def get_runner(cfg=None, evaluator=None, **kw):
    """Daytona when a key is present (and the SDK imports), else the local pool.
    Every degradation is logged with its reason rather than hidden."""
    if os.environ.get("AIRLOCK_RUNNER", "").lower() == "local":
        _log("AIRLOCK_RUNNER=local: using the local pool")
        return LocalRunner(evaluator)
    key = getattr(cfg, "daytona_key", None)
    if key is None:
        key = os.environ.get("DAYTONA_API_KEY", "")
    if not key:
        _log("no DAYTONA_API_KEY: using the local pool")
        return LocalRunner(evaluator)
    try:
        return DaytonaRunner(cfg, evaluator=evaluator, **kw)
    except Exception as exc:
        _log(f"Daytona unavailable ({exc}); using the local pool. "
             f"Install the SDK with `pip install daytona`.")
        return LocalRunner(evaluator)


__all__ = ["Runner", "LocalRunner", "DaytonaRunner", "get_runner", "runner_label"]

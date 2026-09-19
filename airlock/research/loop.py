"""ResearchLoop — the autoresearch methodology as a program.

    loop = ResearchLoop(cfg, runner, emit, lab_id)      # or ResearchLoop(cfg=<app Config>, emit=..., lab_id=...)
    loop.run(prompt)                                     # blocks; emits SPEC §2 events through `emit`
    loop.steer("focus voting") / loop.steer("stop")      # thread-safe, from any thread
    loop.answer(ask_id, option_id)                       # releases a blocked `ask`

What one run does, in order (SPEC §0 / protocol.md / loop-contract.md):

  1. explore   screen every single model raw, one batch → best single = control/champion.
  2. confirm   rounds. Each round picks a batch of *declared* hypotheses — one factor
               or one atomic recipe each, with its own control and a falsifier written
               before the run — and gates each result:
                   promote   ≥ min_delta over the champion (champion replaced)
                   keep      ≥ min_delta over its control (that member's new best)
                   repeat    0 < Δ < min_delta on the champion: one paired re-run on a
                             second fold seed, then gated on the paired mean
                   discard   otherwise (incl. superadditive harm, which also seals the combo)
                   crash     evaluator error — not scientific evidence
  3. families  every non-crash result is evidence for the hypothesis' declared family at
               its order (1 isolated / 2 pair / 3+ triple). A family whose isolated, pair
               and triple evidence all miss the bar — and which never passed — is sealed:
               no recipe containing it is legal again. A recipe miss never closes the
               isolated factors inside it.
  4. stall     no promote for K rounds → why-critique: a grade, two competing
               explanations, the observation that separates them, and the next experiment
               = the cheapest unresolved candidate that can falsify the leading one and
               passes the information-gain gate (no sealed-family recombination, no
               closed-bracket retry, no comfort recipe, no zero-information cell). Nothing
               qualifies → HOLD: the loop stops burning evals and writes its analysis.
  5. steering  `stop`, `focus <family>`, `skip <family>`, `why <spec|family>`,
               `run <n> more`; `ask` before an expensive batch (blocks with a timeout).

Every event is a dict with `type` and the fields of SPEC §2. Extra fields are
additive (`hypothesis_id`, `spec_id`, `runtime`, `stats`) and never replace a
schema field.
"""
import json
import math
import queue
import threading
import time
import uuid

from . import catalog as C
from .config import ResearchConfig, STAGNATION_GRADES
from .ledger import Ledger
from ..llm import get_llm

GRADE_PRIORITY = ("superadditive_harm", "information_gain", "family_tax",
                  "measurement_resolution", "frozen_axis", "local_optimum")
TIER_NAMES = {0: "explore", 1: "transform", 2: "hyper", 3: "ensemble", 4: "characterize", 5: "chain"}

# a scaler cannot change an axis-aligned or per-class-gaussian learner: zero expected information
_SCALE_INVARIANT = {"trees", "bayes"}
# knobs that regularise each model, strongest first (characterization triples take the
# first one the champion is not already using)
_REGULARIZERS = {"logistic": [("C", 0.1), ("penalty", "l1"), ("class_weight", "balanced")],
                 "linsvm": [("C", 0.1)], "mlp": [("alpha", 0.01), ("hidden_layer_sizes", 64)],
                 "rf": [("max_depth", 6), ("n_estimators", 200)], "gb": [("learning_rate", 0.03), ("max_iter", 200)],
                 "tree": [("max_depth", 3), ("max_depth", 8)], "knn": [("n_neighbors", 15), ("weights", "distance")],
                 "nb": [("var_smoothing", 0.001)]}


def _regularizer(model, hyper):
    for k, v in _REGULARIZERS.get(model, []):
        if k not in (hyper or {}):
            return k, v
    return None


class Hypothesis:
    __slots__ = ("id", "kind", "family", "control", "treatment", "spec_id", "control_id",
                 "cost_est", "why", "falsifier", "order", "knobs", "tier", "falsifies",
                 "text", "tags", "seed", "repeat_of")

    def __init__(self, kind, family, control, treatment, why, falsifier, tier,
                 falsifies=(), tags=(), text=None):
        self.id = None
        self.kind = kind
        self.family = family
        self.control = control
        self.treatment = treatment
        self.spec_id = C.recipe_id(treatment)
        self.control_id = C.recipe_id(control) if control else None
        self.cost_est = C.cost_estimate(treatment)
        self.why = why
        self.falsifier = falsifier
        self.knobs = C.knobs_changed(control, treatment) if control else sorted(C.components(treatment))
        self.order = C.treatment_order(control, treatment)
        self.tier = tier
        self.falsifies = set(falsifies)
        self.tags = set(tags)
        self.text = text or C.describe(treatment)
        self.seed = None
        self.repeat_of = None

    def sort_key(self):
        return (self.tier, self.cost_est, C.composite_order(self.treatment), self.spec_id)


class InlineRunner:
    """Sequential fallback so the engine runs with no runners.py at all."""
    label = "local"

    def __init__(self, evaluator=None):
        self._eval = evaluator

    def evaluate_many(self, specs, on_result=None):
        if self._eval is None:
            from .evaluate import evaluate
            self._eval = evaluate
        out = []
        for s in specs:
            t0 = time.time()
            try:
                r = self._eval(s["recipe"], int(s.get("seed", 0)))
                res = {"spec_id": s["spec_id"], "metric": float(r["metric"]), "std": float(r.get("std") or 0.0),
                       "cost_ms": int(r.get("cost_ms") or 0), "params": r.get("params"),
                       "runtime": {"sandbox": "local", "sandbox_id": None,
                                   "ms": int((time.time() - t0) * 1000)}, "error": None}
            except Exception as exc:
                res = {"spec_id": s["spec_id"], "metric": None, "std": None, "cost_ms": 0, "params": None,
                       "runtime": {"sandbox": "local", "sandbox_id": None,
                                   "ms": int((time.time() - t0) * 1000)},
                       "error": f"{type(exc).__name__}: {exc}"}
            out.append(res)
            if on_result:
                on_result(res)
        return out

    def teardown(self):
        pass


def _fmt(x, nd=3):
    return "—" if x is None else f"{x:.{nd}f}"


def _sd(d):
    return "—" if d is None else f"{d:+.3f}"


class ResearchLoop:
    def __init__(self, cfg=None, runner=None, emit=None, lab_id=None, prompt=None,
                 task=None, budget=None, seed=None):
        if isinstance(cfg, ResearchConfig):
            self.cfg, self.app_cfg = cfg, None
        else:
            self.app_cfg = cfg
            self.cfg = ResearchConfig.from_app(cfg)
        if budget:
            self.cfg.max_evals = int(budget)
        if seed is not None:
            self.cfg.seed = int(seed)
        if task and task in ("breast_cancer", "breast_cancer_noisy", "synthetic"):
            self.cfg.dataset = task
        self.prompt = prompt or ""
        self.lab_id = lab_id or uuid.uuid4().hex[:10]
        self.emit_fn = emit or (lambda e: None)
        self._runner = runner
        self._own_runner = runner is None
        self.ledger = Ledger(self.lab_id, self.cfg.runs_dir)

        # science state
        self.results = {}          # spec_id -> {seed: result}
        self.recipes = {}          # spec_id -> recipe
        self.evals_used = 0
        self.grid = C.grid_size()
        self.champion = None       # {'spec_id','recipe','metric','std','seeds'}
        self.best_single = None
        self.singles = []          # [(model, metric)] from the explore screen, best first
        self.families = {f: {"status": "open", "evidence": [], "passes": 0, "region": None}
                         for f in C.FAMILIES}
        self.sealed_combos = {}    # frozenset(tokens) -> family that declared the harmful axis
        self.harms = []
        self.hyps = {}             # hypothesis id -> Hypothesis
        self.hyp_by_spec = {}      # spec_id -> hypothesis id (first that tested it)
        self.declined = set()      # spec ids the human skipped at an ask
        self.repeated = set()      # spec ids that already got their paired repeat
        self.improved_members = [] # models whose best recipe improved via a `keep`
        self.round = 0
        self.rounds_since_improve = 0
        self.history = []
        self.window = []           # recent (round, hypothesis, gate, delta, champ_delta) for critiques
        self.critiques = []
        self.leading = None        # current leading explanation (grade) while stalled
        self._critiqued_round = -1
        self.status = "created"
        self._seq = 0
        self._batch_seq = 0
        # Optional open-model layer (Nosana / any OpenAI-compatible endpoint). Never scores:
        # it answers `why` questions from the ledger and writes the closing insight.
        self.llm = get_llm()
        self.llm.timeout = 20

        # steering / asks (thread-safe)
        self.inbox = queue.Queue()
        self._stop = threading.Event()
        self.focus = None
        self.skipped = set()
        self._asks = {}
        self._asked = set()
        self._ask_cond = threading.Condition()
        self.pending_ask = None

    # ------------------------------------------------------------ plumbing --
    @property
    def runner(self):
        if self._runner is None:
            try:
                from .runners import get_runner
                self._runner = get_runner(self.app_cfg)
            except Exception:
                self._runner = InlineRunner()
        return self._runner

    def _emit(self, type_, **fields):
        e = {"type": type_, "ts": round(time.time(), 3)}
        e.update(fields)
        try:
            self.emit_fn(e)
        except Exception:
            pass
        return e

    def _say(self, text, cards=None):
        e = {"text": text}
        if cards:
            e["cards"] = cards
        return self._emit("agent_message", **e)

    def _next_id(self, prefix):
        self._seq += 1
        return f"{prefix}{self._seq}"

    # ------------------------------------------------------------ steering --
    def steer(self, text):
        """Queue a steering message (thread-safe). Returns an ack."""
        text = (text or "").strip()
        if text:
            self.inbox.put(text)
        return {"queued": self.inbox.qsize(), "status": self.status}

    def answer(self, ask_id, option_id):
        with self._ask_cond:
            ask = self._asks.get(ask_id)
            if ask is None:
                return {"ok": False, "error": "unknown ask"}
            ask["answer"] = option_id
            self._ask_cond.notify_all()
        return {"ok": True, "ask_id": ask_id, "option_id": option_id}

    def _ask(self, question, options, reason, default=None):
        """Emit an ask and block until answered, steered to stop, or timed out."""
        ask_id = self._next_id("ask")
        default = default or self.cfg.ask_default
        with self._ask_cond:
            self._asks[ask_id] = {"answer": None, "options": [o["id"] for o in options]}
        self.pending_ask = ask_id
        self._emit("ask", id=ask_id, question=question, options=options, reason=reason)
        deadline = time.time() + max(0.0, float(self.cfg.ask_timeout_s))
        with self._ask_cond:
            while self._asks[ask_id]["answer"] is None and not self._stop.is_set():
                left = deadline - time.time()
                if left <= 0:
                    break
                self._ask_cond.wait(timeout=min(0.25, left))
            ans = self._asks[ask_id]["answer"]
        self.pending_ask = None
        if self._stop.is_set():
            return "skip"
        if ans is None:
            self._say(f"No answer in {int(self.cfg.ask_timeout_s)}s — taking the default: {default}.")
            return default
        return ans

    def _process_inbox(self):
        while True:
            try:
                text = self.inbox.get_nowait()
            except queue.Empty:
                return
            self._handle_steer(text)

    def _handle_steer(self, text):
        t = text.strip()
        low = t.lower()
        words = low.split()
        if not words:
            return
        if words[0] in ("stop", "halt", "abort"):
            self._stop.set()
            self._say("Stopping after the current batch. The ledger keeps everything measured so far.")
            return
        if words[0] in ("focus", "prioritize", "prioritise") and len(words) > 1:
            fam = self._match_family(" ".join(words[1:]))
            if fam:
                self.focus = fam
                self.skipped.discard(fam)
                self._say(f"Focusing on `{fam}` — its candidates run first; nothing else is forbidden.")
            else:
                self._say(f"No family or group called `{' '.join(words[1:])}`. Families: "
                          + ", ".join(sorted(C.FAMILIES)) + ".")
            return
        if words[0] in ("skip", "avoid", "ignore") and len(words) > 1:
            fam = self._match_family(" ".join(words[1:]))
            if fam:
                self.skipped.add(fam)
                if self.focus == fam:
                    self.focus = None
                self._say(f"Skipping `{fam}` — no candidate from it will be launched. "
                          f"Say `focus {fam}` to reopen it.")
            else:
                self._say(f"No family or group called `{' '.join(words[1:])}`.")
            return
        if words[0] == "why" and len(words) > 1:
            self._explain(" ".join(t.split()[1:]))
            return
        if words[0] == "run" and len(words) >= 2:
            n = None
            for w in words[1:]:
                if w.isdigit():
                    n = int(w)
                    break
            if n:
                self.cfg.max_evals += n
                self._stop.clear()
                self._say(f"Budget extended by {n} evals → {self.cfg.max_evals} total.")
                return
        self._say("I can take: `stop`, `focus <family>`, `skip <family>`, `why <spec or family>`, "
                  "`run <n> more`.")

    def _match_family(self, name):
        name = name.strip().strip("`'\"")
        if name in C.FAMILIES:
            return name
        for f in C.FAMILIES:
            if f.endswith("." + name) or f == name.replace(" ", "."):
                return f
        # a group name → its first open family? No: groups are not sealable units. Map the
        # obvious aliases instead.
        alias = {"ensemble": "voting", "ensembles": "voting", "vote": "voting", "stack": "stacking",
                 "bag": "bagging", "poly": "polynomial", "scale": "scaling", "scaler": "scaling",
                 "pca": "reduction", "kbest": "reduction"}
        if name in alias:
            return alias[name]
        for f in C.FAMILIES:
            if name in f:
                return f
        return None

    def _explain(self, what):
        what = what.strip().strip("`'\"")
        fam = what if what in C.FAMILIES else None
        if fam:
            st = self.families[fam]
            rows = [{"ok": e["gate"] in ("promote", "keep"),
                     "mark": "✓" if e["gate"] in ("promote", "keep") else "✗",
                     "text": f"{C.order_name(e['order'])} · {e['text']}  {_fmt(e['metric'])} ({_sd(e['delta'])})"}
                    for e in st["evidence"]]
            head = {"open": "open", "sealed": "sealed — isolated, pair and triple all missed the bar",
                    "skipped": "skipped by you"}[st["status"]]
            self._say(f"`{fam}` is {head}. {C.FAMILIES[fam].mechanism}.",
                      cards=[{"kind": "checklist", "title": f"Evidence for `{fam}`", "rows": rows}] if rows else None)
            self._llm_explain(what, self.ledger.find(family=fam))
            return
        # a spec id or a description fragment
        hits = [sid for sid in self.results if what == sid or what in sid or what in C.describe(self.recipes[sid])]
        if not hits:
            self._say(f"Nothing measured matches `{what}`. Ask with a spec id like "
                      f"`standardize|logistic` or a family like `polynomial`.")
            return
        cards = []
        for sid in hits[:3]:
            recs = self.ledger.find(spec_id=sid)
            if not recs:
                continue
            r = recs[-1]
            cards.append({"kind": r["gate"] if r["gate"] in ("promote", "keep", "discard", "crash") else "discarded",
                          "title": f"{r['gate']} → {C.describe(self.recipes[sid])}  {_fmt(r.get('metric'))} ({_sd(r.get('delta'))})",
                          "detail": r.get("reason"), "spec_id": sid, "metric": r.get("metric"), "delta": r.get("delta")})
        self._say(f"What happened to `{what}`:", cards=cards)
        self._llm_explain(what, [r for sid in hits[:3] for r in self.ledger.find(spec_id=sid)])

    def _llm_explain(self, question, rows):
        """Open-model answer grounded in ledger rows (only when an endpoint is configured)."""
        if not self.llm.available():
            return
        ch = self.champion
        champ = f"{C.describe(ch['recipe'])} {_fmt(ch['metric'])}" if ch else None
        slim = [{k: r.get(k) for k in ("spec_id", "family", "gate", "metric", "delta", "reason") if k in r}
                for r in rows[-40:]]
        ans = self.llm.explain(question, slim, champion=champ)
        if ans and not ans.startswith("("):
            self._say(f"{self.llm.provider} · {ans}")

    def _llm_insight(self, summary, stats):
        """One closing paragraph from the open model, written from the measured summary."""
        if not self.llm.available():
            return
        try:
            sys_p = ("You are the research agent's closing note. Given a measured summary of a structure "
                     "search, write 2 short sentences: what the winning structure is and what the failed "
                     "composites teach. Never invent numbers; reuse the ones given. No emojis.")
            harms = [C.describe(h["recipe"]) if isinstance(h, dict) and "recipe" in h else str(h) for h in self.harms[:6]]
            user = json.dumps({"summary": summary, "sealed": stats.get("sealed"), "harmful_composites": harms,
                               "evals": stats.get("evals_used"), "grid": stats.get("evals_grid")}, ensure_ascii=False)
            note = self.llm.chat([{"role": "system", "content": sys_p}, {"role": "user", "content": user}],
                                 temperature=0.2, max_tokens=160)
            if note:
                self._say(f"{self.llm.provider} · {note}")
        except Exception:
            pass

    # ------------------------------------------------------------ helpers --
    def _metric(self, spec_id, seed=None):
        """Metric of record: the primary fold seed's (repeat seeds only ever feed the
        paired delta, so a champion's number never drifts after a repeat)."""
        runs = self.results.get(spec_id) or {}
        ok = {s: r["metric"] for s, r in runs.items() if r.get("metric") is not None}
        if not ok:
            return None
        if seed is not None:
            return ok.get(seed)
        if self.cfg.seed in ok:
            return ok[self.cfg.seed]
        return ok[min(ok)]

    def _paired_delta(self, a, b):
        """Mean of (a − b) over the seeds both were measured on; None if none."""
        ra, rb = self.results.get(a) or {}, self.results.get(b) or {}
        common = [s for s in ra if s in rb and ra[s].get("metric") is not None and rb[s].get("metric") is not None]
        if not common:
            return None
        return sum(ra[s]["metric"] - rb[s]["metric"] for s in common) / len(common)

    def _measured(self, spec_id):
        return self._metric(spec_id) is not None

    def _best_of_model(self, model, transform=None):
        """(spec_id, metric) of the best measured non-ensemble recipe led by `model`."""
        best = (None, None)
        for sid, r in self.recipes.items():
            if r["model"] != model or r.get("ensemble"):
                continue
            if transform is not None and (r.get("transform") or "none") != transform:
                continue
            m = self._metric(sid)
            if m is not None and (best[1] is None or m > best[1]):
                best = (sid, m)
        return best

    def _best_member(self, recipe):
        """(spec_id, metric) of the best measured strict sub-recipe."""
        best = (None, None)
        for sid, r in self.recipes.items():
            if C.is_sub_recipe(r, recipe):
                m = self._metric(sid)
                if m is not None and (best[1] is None or m > best[1]):
                    best = (sid, m)
        return best

    def _sealed(self):
        return [f for f, st in self.families.items() if st["status"] == "sealed"]

    def _legal(self, recipe):
        return C.check_legal(recipe, self._sealed(), list(self.sealed_combos.items()))

    def _champion_metric(self):
        return self._metric(self.champion["spec_id"]) if self.champion else None

    def _model_rank(self):
        """Models ordered by their best measured single, best first."""
        scored = []
        for m in C.MODELS:
            _, v = self._best_of_model(m)
            scored.append((-(v if v is not None else -1), m))
        return [m for _, m in sorted(scored)]

    # ------------------------------------------------------------ run --
    def run(self, prompt=None):
        if prompt:
            self.prompt = prompt
        self.status = "running"
        t0 = time.time()
        try:
            self._start()
            self._explore()
            while self._round():
                if self.cfg.pace_ms:
                    time.sleep(self.cfg.pace_ms / 1000.0)
        except Exception as exc:
            self.status = "error"
            self._say(f"The lab hit an error and stopped: {type(exc).__name__}: {exc}")
            self._emit("done", summary=f"error: {exc}", stats={"status": "error"})
            raise
        finally:
            if self._own_runner and self._runner is not None:
                try:
                    self._runner.teardown()
                except Exception:
                    pass
        return self._finish(time.time() - t0)

    def _start(self):
        from .evaluate import dataset_info
        info = dataset_info(self.cfg)
        summary = C.catalog_summary()
        self._emit("lab_started", lab_id=self.lab_id,
                   task={"dataset": info["name"], "desc": info["desc"], "n": info["n"], "d": info["d"],
                         "prompt": self.prompt},
                   metric=f"{self.cfg.folds}-fold {self.cfg.metric}",
                   catalog_summary=summary,
                   providers={"runner": self.runner.label, "sandbox": self.runner.label,
                              "llm": self.llm.provider if self.llm.available() else "templates"},
                   budget={"max_evals": self.cfg.max_evals, "min_delta": self.cfg.min_delta,
                           "stagnation_k": self.cfg.stagnation_k, "grid": self.grid, "seed": self.cfg.seed})
        self._say(f"Starting on `{info['desc']}` · metric: {self.cfg.folds}-fold {self.cfg.metric} · "
                  f"catalog: {summary['factors']} factors in {len(summary['groups'])} groups "
                  f"({len(summary['families'])} families) · bar: +{self.cfg.min_delta:.3f} · "
                  f"budget: {self.cfg.max_evals} evals against a {self.grid:,}-cell grid · "
                  f"runner: {self.runner.label}.")

    # ------------------------------------------------------------ explore --
    def _explore(self):
        control = C.make_recipe("none", C.MODELS[0])
        hyps = []
        for m in C.MODELS:
            h = Hypothesis("factor", C.family_of(m), None, C.make_recipe("none", m),
                           why="explore screen: raw features, default knobs — candidates only, never a promotion",
                           falsifier="a screen never promotes; it only ranks", tier=0, tags={"explore"})
            hyps.append(h)
        hid = self._next_id("h")
        self._emit("hypothesis", id=hid, text=f"Explore screen: {len(hyps)} single models on raw features, one batch.",
                   kind="factor", family="model", control="—",
                   treatment=", ".join(C.MODELS), cost_est=sum(h.cost_est for h in hyps),
                   why="establish the control: the best single model", falsifier="none (explore lane)")
        for h in hyps:
            h.id = hid
            self.hyps.setdefault(hid, h)
        self._say(f"Explore screen: {len(hyps)} single models, raw features, one batch of {len(hyps)}.")
        results = self._evaluate(hyps, lane="explore")
        rows = []
        for h, res in zip(hyps, results):
            gate = "crash" if res.get("error") else "explore_only"
            metric = res.get("metric")
            self._emit("experiment_result", id=res["_exp_id"], metric=metric, std=res.get("std"),
                       delta_vs_control=None, cost_ms=res.get("cost_ms"), gate=gate,
                       reason=res.get("error") or "explore screen: ranked, not promoted",
                       hypothesis_id=hid, spec_id=h.spec_id, spec=h.treatment, runtime=res.get("runtime"))
            self.ledger.append({"round": 0, "lane": "explore", "hypothesis_id": hid, "kind": "factor",
                                "family": h.family, "order": 0, "knobs": [], "spec_id": h.spec_id,
                                "spec": h.treatment, "control_spec_id": None, "control_metric": None,
                                "metric": metric, "std": res.get("std"), "delta": None, "gate": gate,
                                "reason": res.get("error") or "explore screen", "cost_ms": res.get("cost_ms"),
                                "runtime": res.get("runtime"), "seed": self.cfg.seed})
            rows.append({"ok": gate != "crash", "mark": "·" if gate != "crash" else "✗", "metric": metric,
                         "spec_id": h.spec_id, "gate": gate,
                         "text": f"{h.treatment['model']}  {_fmt(metric)}" + (f" ± {res['std']:.3f}" if res.get("std") is not None else "")
                                 + (f"  crash: {res['error']}" if res.get("error") else "")})
        ranked = sorted(((self._metric(h.spec_id), h) for h in hyps if self._measured(h.spec_id)),
                        key=lambda x: (-x[0], C.composite_order(x[1].treatment), x[1].spec_id))
        if not ranked:
            raise RuntimeError("every single model crashed in the explore screen")
        self.singles = [(h.treatment["model"], m) for m, h in ranked]
        best_m, best_h = ranked[0]
        self.best_single = {"spec_id": best_h.spec_id, "recipe": best_h.treatment, "metric": best_m,
                            "std": self.results[best_h.spec_id][self.cfg.seed].get("std")}
        self.champion = {"spec_id": best_h.spec_id, "recipe": best_h.treatment, "metric": best_m,
                         "std": self.best_single["std"], "seeds": [self.cfg.seed], "promoted_round": 0,
                         "evals_used": self.evals_used}
        self.ledger.set_champion(self._champion_record())
        rows.sort(key=lambda r: -(r.get("metric") if r.get("metric") is not None else -1))
        self._say(f"Control → {best_h.treatment['model']} {_fmt(best_m)} (best single). "
                  f"Every confirm from here is measured against its own control and promoted only over this champion.",
                  cards=[{"kind": "checklist", "title": "Explore screen — single models, raw", "rows": rows}])
        self._emit_champion(delta_vs_best_single=0.0)
        self._progress(batch_metrics=[m for m, _ in ranked])

    # ------------------------------------------------------------ rounds --
    def _round(self):
        """One confirm round. Returns False when the lab is over."""
        self._process_inbox()
        if self._stop.is_set():
            self.status = "stopped"
            return False
        if self.evals_used >= self.cfg.max_evals:
            self.status = "budget"
            return False
        self.round += 1
        stalled = self.rounds_since_improve >= self.cfg.stagnation_k
        cands = self._candidates()
        if not cands:
            self._hold(cands)
            self.status = "hold"
            return False
        if stalled and (self.leading is None or (self.rounds_since_improve - self.cfg.stagnation_k) % self.cfg.stagnation_k == 0):
            self.leading = self._critique(cands)
        batch, mode = self._select(cands)
        if not batch and self.leading and self._critiqued_round != self.round:
            # the leading explanation has no falsifier left: re-grade with what we know now
            self.leading = self._critique(cands, regrade=True)
            batch, mode = self._select(cands)
        if not batch:
            self._hold(cands)
            self.status = "hold"
            return False
        room = self.cfg.max_evals - self.evals_used
        if room <= 0:
            self.status = "budget"
            return False
        batch = batch[:max(1, min(len(batch), room))]
        if not self._approve(batch):
            for h in batch:
                self.declined.add(h.spec_id)
            self._say("Skipped that batch. Looking for the next cheapest candidate.")
            return True
        self._confirm(batch, mode)
        return True

    # ------------------------------------------------------------ candidates --
    def _candidates(self):
        """Every legal, untested, information-bearing hypothesis available now."""
        out = []
        ch = self.champion["recipe"]
        ch_parts = C.transform_parts(ch)
        lead = ch["model"]
        rank = self._model_rank()
        tops = [m for m in rank[:self.cfg.top_singles]]
        if lead not in tops:
            tops.append(lead)

        def add(h):
            if h.spec_id in self.results or h.spec_id in self.declined:
                return                                          # closed bracket: measured or refused
            ok, why = self._legal(h.treatment)
            if not ok:
                return
            fam_state = self.families[h.family]
            if fam_state["status"] != "open" or h.family in self.skipped:
                return
            if self._is_comfort(h):
                return
            if any(x.spec_id == h.spec_id for x in out):
                return
            out.append(h)

        # ---- tier 1: one transform knob on the champion, and on each top single (own control)
        for m in tops:
            if m == lead:
                base, base_parts = ch, ch_parts
            else:
                base = C.make_recipe("none", m)
                base_parts = []
            for t in C.TRANSFORMS:
                if t == "none" or t in base_parts:
                    continue
                fam = C.family_of(t)
                if fam == "scaling" and C.family_of(m) in _SCALE_INVARIANT:
                    continue                                    # zero expected information
                if fam == "scaling" and any(p in C.SCALERS for p in base_parts):
                    parts = [t] + [p for p in base_parts if p not in C.SCALERS]     # swap the scaler
                    why = f"swap the scaler: {t} maps columns to a normal shape instead of z-scores"
                else:
                    parts = base_parts + [t]
                    if t in C.SCALERS:
                        parts = [t] + base_parts
                    why = {"scaling": f"{m} is scale-sensitive; raw columns span 5 orders of magnitude",
                           "polynomial": f"pairwise interactions may be what {m} is missing",
                           "reduction": f"20 of 50 columns are noise; {t} should drop or fold them"}[fam]
                treat = dict(base, transform=">".join(parts))
                treat = C.make_recipe(treat["transform"], treat["model"], treat.get("ensemble"),
                                      treat.get("hyper"), treat.get("members"))
                h = Hypothesis("factor", fam, base, treat, why=why,
                               falsifier=f"discard if Δ < +{self.cfg.min_delta:.3f} vs {C.describe(base)}",
                               tier=1, falsifies={"local_optimum"} if m == lead else set(),
                               tags={"transform", "on_champion" if m == lead else "on_single"})
                add(h)

        # ---- tier 2: one hyper knob on the champion's lead model
        for f in C.hyper_factors_for(lead):
            fac = C.FACTORS[f]
            if fac.param in (ch.get("hyper") or {}):
                continue
            treat = C.make_recipe(ch["transform"], lead, ch.get("ensemble"),
                                  dict(ch.get("hyper") or {}, **{fac.param: fac.value}), ch.get("members"))
            h = Hypothesis("factor", fac.family, ch, treat,
                           why=f"{fac.desc}: a nearby knob on the champion's learner",
                           falsifier=f"discard if Δ < +{self.cfg.min_delta:.3f} vs champion",
                           tier=2, tags={"hyper", "on_champion"})
            add(h)

        # ---- tier 3: ensembles over the champion (atomic recipes)
        self._ensemble_candidates(add, ch, rank)

        # ---- tier 4: family characterization (pair / triple after isolated misses)
        self._characterization_candidates(add, ch)

        return out

    def _ensemble_candidates(self, add, ch, rank):
        lead = ch["model"]
        T = ch["transform"]
        ens = ch.get("ensemble")
        members = list(ch.get("members") or [])
        partners = [m for m in rank if m != lead and m not in members]
        fam_of = C.family_of
        fals = {"local_optimum", "measurement_resolution"}

        def improved_tag(mem):
            # only a member improved *under the champion's own transform* is really inside this composite
            return {"information_gain"} if any(m in self.improved_members and self._best_of_model(m, T)[0]
                                               for m in mem) else set()

        def vote(mem, why, tag):
            mem = sorted(set(mem))
            if lead not in mem:
                mem = sorted(set(mem) | {lead})
            treat = C.make_recipe(T, lead, "soft_vote", ch.get("hyper"), mem)
            add(Hypothesis("recipe", "voting", ch, treat, why=why,
                           falsifier=f"discard if below champion + {self.cfg.min_delta:.3f}; "
                                     f"superadditive harm if below its best member − {self.cfg.min_delta:.3f}",
                           tier=3, falsifies=fals | improved_tag(mem), tags={"ensemble", tag}))

        def stack(mem, meta, why, tag):
            mem = sorted(set(mem))
            treat = C.make_recipe(T, meta, "stacking", None, mem)
            add(Hypothesis("recipe", "stacking", ch, treat, why=why,
                           falsifier=f"discard if below champion + {self.cfg.min_delta:.3f}; "
                                     f"superadditive harm if below its best member − {self.cfg.min_delta:.3f}",
                           tier=3, falsifies=fals | improved_tag(mem), tags={"ensemble", tag}))

        if ens is None:
            if partners:
                p1 = partners[0]
                vote([lead, p1], f"the two strongest learners ({lead}, {p1}) err on different rows; averaging probabilities should cancel some errors", "top2")
                stack([lead, p1], "logistic", f"a meta learner can weight {lead} and {p1} by out-of-fold evidence instead of averaging", "top2")
            if len(partners) >= 2:
                vote([lead] + partners[:2], "three strongest learners: more diversity for the average", "top3")
                stack([lead] + partners[:2], "logistic", "three strongest learners under a logistic meta learner", "top3")
            if len(partners) >= 3:
                vote([lead] + partners[:3], "four strongest learners: does diversity keep paying?", "top4")
            # diversity hypotheses: one learner per model family, regardless of its own accuracy
            seen = {fam_of(lead)}
            diverse = []
            for m in partners:
                if fam_of(m) not in seen:
                    seen.add(fam_of(m))
                    diverse.append(m)
            if diverse:
                vote([lead, diverse[-1] if len(diverse) > 1 else diverse[0]],
                     "a weaker but differently-biased partner: error diversity may matter more than member accuracy", "diverse2")
            if len(diverse) >= 2:
                vote([lead] + diverse[:3], "one learner per family: maximal error diversity", "diverse")
            bag = C.make_recipe(T, lead, "bagging", ch.get("hyper"))
            add(Hypothesis("recipe", "bagging", ch, bag,
                           why=f"bootstrap copies of {lead} trade a little bias for variance",
                           falsifier=f"discard if below champion + {self.cfg.min_delta:.3f}",
                           tier=3, falsifies=fals, tags={"ensemble", "bag"}))
        elif ens == "soft_vote":
            if partners and len(members) < C.MAX_MEMBERS:
                vote(members + [partners[0]], f"grow the vote with the next strongest learner ({partners[0]})", "grow")
            stack(members, "logistic", "the same members under a meta learner instead of a plain average", "restack")
        elif ens == "stacking":
            if partners and len(members) < C.MAX_MEMBERS:
                stack(members + [partners[0]], lead, f"grow the stack with {partners[0]}", "grow")
            vote(members, "the same members under a plain average — is the meta learner earning its keep?", "revote")
        elif ens == "bagging":
            if partners:
                base = C.make_recipe(T, lead)
                vote([lead, partners[0]], f"the bagged lead is a variance story; {partners[0]} adds a bias story", "top2")
                stack([lead, partners[0]], "logistic", f"stack {lead} with {partners[0]}", "top2")
        # drop-the-weakest falsifiers for recorded harms
        for harm in self.harms:
            r = harm["recipe"]
            if r.get("ensemble") not in ("soft_vote", "stacking") or len(r.get("members") or []) <= 2:
                continue
            weakest = min(r["members"], key=lambda m: (self._best_of_model(m)[1] or 0.0, m))
            mem = [m for m in r["members"] if m != weakest]
            if r["ensemble"] == "soft_vote" and r["model"] == weakest:
                continue
            treat = C.make_recipe(r["transform"], r["model"], r["ensemble"], r.get("hyper"), mem)
            add(Hypothesis("recipe", C.family_of(r["ensemble"]), ch, treat,
                           why=f"drop the weakest member ({weakest}) of a harmful composite: if the harm was that member alone, this recovers",
                           falsifier="if this also lands below its best member, the harm is interaction, not one member",
                           tier=3, falsifies={"superadditive_harm"}, tags={"ensemble", "drop_weakest"}))

    def _characterization_candidates(self, add, ch):
        """Pair / triple recipes for families whose isolated evidence exists and all missed."""
        lead = ch["model"]
        parts = C.transform_parts(ch)
        reg = _regularizer(lead, ch.get("hyper"))
        for fam, st in self.families.items():
            if st["status"] != "open" or st["passes"] or not st["evidence"]:
                continue
            orders = {e["order"] for e in st["evidence"]}
            if 1 not in orders:
                continue
            need = 2 if 2 not in orders else (3 if 3 not in orders else None)
            if need is None:
                continue
            group = C.FAMILIES[fam].group
            knobs = list(C.FAMILIES[fam].factors)
            treat, why = None, None
            if group == "transform":
                base = [p for p in parts]
                if fam == "polynomial":
                    chain = [p for p in base if p not in ("polynomial", "kbest")] + ["polynomial", "kbest"]
                    hyper = dict(ch.get("hyper") or {})
                    if need == 3 and reg:
                        hyper[reg[0]] = reg[1]
                    treat = C.make_recipe(chain, lead, ch.get("ensemble"), hyper, ch.get("members"))
                    why = ("expand → select: selection prunes the interaction blow-up" if need == 2 else
                           "expand → select → regularise: the last mechanism that could rescue interactions")
                elif fam == "reduction":
                    chain = [p for p in base if p not in ("pca", "kbest")] + ["kbest", "pca"]
                    hyper = dict(ch.get("hyper") or {})
                    if need == 3 and reg:
                        hyper[reg[0]] = reg[1]
                    treat = C.make_recipe(chain, lead, ch.get("ensemble"), hyper, ch.get("members"))
                    why = ("select then fold: drop noise columns before projecting" if need == 2 else
                           "select → fold → regularise: the last mechanism that could make reduction pay")
                elif fam == "scaling":
                    continue                                    # two scalers never chain; scaling seals only on isolated misses
            elif group == "hyper":
                if C.FACTORS[knobs[0]].model != lead:
                    continue
                params = {}
                for k in knobs:
                    f = C.FACTORS[k]
                    params.setdefault(f.param, f.value)
                keys = list(params)[:need]
                if len(keys) < need:
                    continue
                hyper = dict(ch.get("hyper") or {}, **{k: params[k] for k in keys})
                treat = C.make_recipe(ch["transform"], lead, ch.get("ensemble"), hyper, ch.get("members"))
                why = "knobs that missed alone might only pay together (" + ", ".join(keys) + ")"
            elif group == "ensemble":
                continue                                        # pair/triple orders come from tier 3 partners
            if treat is None:
                continue
            if C.treatment_order(ch, treat) < need:
                continue
            add(Hypothesis("recipe", fam, ch, treat, why=why,
                           falsifier=f"if this {C.order_name(need)} also misses the bar, `{fam}` is characterized and sealed",
                           tier=4, falsifies={"family_tax"} if self._is_taxed(fam) else set(),
                           tags={"characterize", fam}))

    def _is_comfort(self, h):
        """A comfort recipe: a lower-order re-cut of an already-failed higher-order
        combination in the same family whose sub-combinations also failed."""
        toks = C.components(h.treatment)
        fam_ev = self.families[h.family]["evidence"]
        failed_supers = [frozenset(e["tokens"]) for e in fam_ev
                         if e["gate"] == "discard" and frozenset(e["tokens"]) > toks]
        if not failed_supers:
            return False
        failed_subs = [frozenset(e["tokens"]) for e in fam_ev
                       if e["gate"] == "discard" and frozenset(e["tokens"]) < toks]
        return bool(failed_subs)

    def _is_taxed(self, fam):
        ev = [e["delta"] for e in self.families[fam]["evidence"] if e["delta"] is not None]
        if len(ev) < 3 or any(d > -self.cfg.min_delta for d in ev):
            return False
        mean = sum(ev) / len(ev)
        spread = math.sqrt(sum((d - mean) ** 2 for d in ev) / len(ev))
        return spread <= max(0.01, 2 * self.cfg.min_delta)

    # ------------------------------------------------------------ selection --
    def _select(self, cands):
        """(batch, mode). Stalled: the cheapest candidates that can falsify the leading
        explanation. Otherwise: the cheapest of the lowest open tier (focus first)."""
        if not cands:
            return [], "none"
        pool = list(cands)
        if self.focus:
            focused = [h for h in pool if h.family == self.focus]
            if focused:
                pool = focused
        if self.leading:
            fals = self._falsifiers(pool, self.leading)
            if fals:
                return fals[:max(1, min(self.cfg.batch_size, 4))], "falsify"
            goal = self._information_goals(pool)
            if goal:
                return goal[:max(1, min(self.cfg.batch_size, 4))], "characterize"
            return [], "stalled"
        tier = min(h.tier for h in pool)
        same = sorted([h for h in pool if h.tier == tier], key=Hypothesis.sort_key)
        return same[:self.cfg.batch_size], TIER_NAMES.get(tier, str(tier))

    @staticmethod
    def _falsifiers(pool, grade):
        """Candidates that can falsify `grade`, cheapest first."""
        return sorted([h for h in pool if grade in h.falsifies],
                      key=lambda h: (h.cost_est, C.composite_order(h.treatment), h.spec_id))

    @staticmethod
    def _information_goals(pool):
        """Candidates with an explicit information goal beyond a win: a pair/triple
        that can seal (or reopen) a family — closing a region of the grid is
        information even when the leading explanation has no falsifier left."""
        return sorted([h for h in pool if "characterize" in h.tags],
                      key=lambda h: (h.cost_est, C.composite_order(h.treatment), h.spec_id))

    def _approve(self, batch):
        """The ask gate: pause before an expensive batch. True = run it."""
        n = len(batch)
        est = sum(h.cost_est for h in batch)
        label = self.runner.label
        threshold = self.cfg.ask_threshold_daytona if label == "daytona" else self.cfg.ask_threshold_local
        expensive = n > threshold
        # locally, the first composite batch is the one expensive step worth a pause
        if not expensive and label == "local" and est > 4000 and any("ensemble" in h.tags for h in batch) \
                and "ask_ensembles" not in self._asked:
            expensive = True
            self._asked.add("ask_ensembles")
        if not expensive:
            return True
        where = f"{min(n, getattr(self.runner, 'pool_size', n))} Daytona sandboxes" if label == "daytona" \
            else f"{min(n, getattr(self.runner, 'workers', n))} local workers"
        fams = sorted({h.family for h in batch})
        q = (f"Run {n} {'/'.join(fams)} recipe{'s' if n > 1 else ''}"
             f"{' in Daytona' if label == 'daytona' else ''}? ~{max(1, round(est / 1000))}s est, {where}.")
        ans = self._ask(q, [{"id": "run", "label": "Run", "detail": f"{n} evals, ~{est} ms estimated"},
                            {"id": "skip", "label": "Skip", "detail": "mark these as declined and pick the next cheapest"}],
                        reason=f"expensive batch: {n} evals" + (" on Daytona" if label == "daytona" else ""))
        return ans == "run"

    # ------------------------------------------------------------ confirm --
    def _confirm(self, batch, mode):
        seed = self.cfg.seed
        for h in batch:
            h.id = self._next_id("h")
            h.seed = seed
            self.hyps[h.id] = h
            self.hyp_by_spec.setdefault(h.spec_id, h.id)
            self._emit("hypothesis", id=h.id, text=h.text, kind=h.kind, family=h.family,
                       control=C.describe(h.control), treatment=C.describe(h.treatment),
                       cost_est=h.cost_est, why=h.why, falsifier=h.falsifier, order=h.order,
                       knobs=h.knobs, control_spec_id=h.control_id, spec_id=h.spec_id,
                       treatment_spec=h.treatment)
        fams = sorted({h.family for h in batch})
        head = {"falsify": f"Round {self.round} · falsifying `{self.leading}`",
                "transform": f"Round {self.round} · one transform knob at a time",
                "hyper": f"Round {self.round} · nearby knobs on the champion",
                "ensemble": f"Round {self.round} · composites over the champion",
                "characterize": f"Round {self.round} · characterizing {', '.join(fams)}",
                "chain": f"Round {self.round} · chains"}.get(mode, f"Round {self.round}")
        self._say(f"{head} — {len(batch)} eval{'s' if len(batch) > 1 else ''}, "
                  f"families: {', '.join(fams)}. Control: {C.describe(self.champion['recipe'])} "
                  f"{_fmt(self._champion_metric())}.")
        # controls not yet measured (shouldn't happen, but a hypothesis may name a fresh control)
        extra = []
        for h in batch:
            if h.control_id and not self._measured(h.control_id) and all(x.spec_id != h.control_id for x in extra):
                extra.append(Hypothesis("factor", h.family, None, h.control, why="control", falsifier="—", tier=h.tier,
                                        tags={"control"}))
        results = self._evaluate(batch + extra, lane="confirm")
        # repeats: sub-bar positive moves on the champion get one paired second-seed run
        champ_id = self.champion["spec_id"]
        champ_before = self._champion_metric()
        repeats = []
        for h, res in zip(batch, results[:len(batch)]):
            if res.get("error") or self.cfg.max_repeats <= 0 or h.spec_id in self.repeated:
                continue
            if h.control_id != champ_id:
                continue
            d = self._paired_delta(h.spec_id, champ_id)
            if d is not None and 0 < d < self.cfg.min_delta and self.evals_used + 2 <= self.cfg.max_evals:
                repeats.append(h)
        if repeats:
            seed2 = seed + 1
            self._say(f"{len(repeats)} move{'s' if len(repeats) > 1 else ''} landed above zero but under the bar — "
                      f"one paired repeat on fold seed {seed2} before judging.",
                      cards=[{"kind": "checklist", "title": "Repeat (paired, second fold seed)",
                              "rows": [{"ok": None, "mark": "↻", "text": f"{h.text}  {_fmt(self._metric(h.spec_id))} "
                                        f"({_sd(self._paired_delta(h.spec_id, champ_id))})"} for h in repeats]}])
            rep_hyps = []
            for h in repeats:
                self.repeated.add(h.spec_id)
                self._emit("experiment_result", id=self.results[h.spec_id][seed]["_exp_id"],
                           metric=self._metric(h.spec_id, seed), std=self.results[h.spec_id][seed].get("std"),
                           delta_vs_control=self._paired_delta(h.spec_id, champ_id),
                           cost_ms=self.results[h.spec_id][seed].get("cost_ms"), gate="repeat",
                           reason=f"0 < Δ < {self.cfg.min_delta:.3f}: paired repeat on seed {seed2}",
                           hypothesis_id=h.id, spec_id=h.spec_id, spec=h.treatment,
                           runtime=self.results[h.spec_id][seed].get("runtime"))
                rh = Hypothesis(h.kind, h.family, h.control, h.treatment, h.why, h.falsifier, h.tier,
                                h.falsifies, h.tags | {"repeat"}, h.text)
                rh.id, rh.seed, rh.repeat_of = h.id, seed2, h.spec_id
                rep_hyps.append(rh)
            if champ_id not in {x.spec_id for x in rep_hyps} and seed2 not in self.results.get(champ_id, {}):
                ctrl = Hypothesis("factor", "identity", None, self.champion["recipe"], "control on the repeat seed",
                                  "—", 0, tags={"control", "repeat"})
                ctrl.id, ctrl.seed = "control", seed2
                rep_hyps.append(ctrl)
            self._evaluate(rep_hyps, lane="confirm", seed=seed2)
        # gate deterministically, champion fixed at the batch start
        gated = []
        for h, res in zip(batch, results[:len(batch)]):
            gated.append((h, res, self._gate(h, res, champ_id, champ_before)))
        promotes = [(h, res, g) for h, res, g in gated if g["gate"] == "promote"]
        winner = None
        if promotes:
            promotes.sort(key=lambda x: (-self._metric(x[0].spec_id), C.composite_order(x[0].treatment), x[0].spec_id))
            winner = promotes[0][0]
            for h, res, g in promotes[1:]:
                g["gate"] = "keep"
                g["reason"] = (f"beat the champion by {g['champ_delta']:+.3f} but lost the tie to "
                               f"{C.describe(winner.treatment)} ({'higher' if self._metric(h.spec_id) < self._metric(winner.spec_id) else 'simpler'})")
        rows = []
        cards = []
        any_promote = False
        any_science = False
        for h, res, g in gated:
            self._apply(h, res, g, winner)
            rows.append(self._row(h, g))
            if g["gate"] == "promote":
                any_promote = True
            if g["gate"] != "crash":
                any_science = True
            if g.get("harm"):
                cards.append({"kind": "harm", "title": f"Harm → {h.text}  {_fmt(self._metric(h.spec_id))} "
                              f"({g['harm']['delta']:+.3f} vs its best member {C.describe(self.recipes[g['harm']['member']])})",
                              "detail": "sealed this exact combination; members stay open", "spec_id": h.spec_id,
                              "metric": self._metric(h.spec_id), "delta": g["harm"]["delta"]})
        # seals after the whole batch has been charged
        for fam in sorted({h.family for h, _, _ in gated}):
            card = self._check_seal(fam)
            if card:
                cards.append(card)
                rows.append({"ok": False, "mark": "⊘", "text": card["title"]})
        summary = [{"kind": "checklist", "title": head, "rows": rows}] + cards
        if winner is not None:
            summary.append(self._champion_card())
        self._say(self._round_line(gated, winner), cards=summary)
        if any_science:
            if any_promote:
                self.rounds_since_improve = 0
                self.leading = None
            else:
                self.rounds_since_improve += 1
        self._progress(batch_metrics=[self._metric(h.spec_id) for h, _, g in gated if g["gate"] != "crash"])
        self.ledger.save_state(self._state())

    def _row(self, h, g):
        m = self._metric(h.spec_id)
        d = g.get("delta")
        gate = g["gate"]
        mark = {"promote": "★", "keep": "✓", "discard": "✗", "crash": "‼", "repeat": "↻"}.get(gate, "·")
        verb = {"promote": "Promoted", "keep": "Kept", "discard": "Discarded", "crash": "Crashed"}.get(gate, gate)
        txt = f"{verb} → {h.text}  {_fmt(m)} ({_sd(d)} vs control)"
        if gate == "crash":
            txt = f"Crashed → {h.text}: {g['reason']}"
        elif g.get("harm"):
            txt += "  ⚠ harm"
        return {"ok": gate in ("promote", "keep"), "mark": mark, "text": txt, "spec_id": h.spec_id,
                "gate": gate, "metric": m, "delta": d}

    def _round_line(self, gated, winner):
        n = len(gated)
        counts = {}
        for _, _, g in gated:
            counts[g["gate"]] = counts.get(g["gate"], 0) + 1
        parts = [f"{v} {k}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        line = f"Round {self.round}: {', '.join(parts)} of {n}."
        if winner is not None:
            line += (f" New champion: {C.describe(winner.treatment)} {_fmt(self._metric(winner.spec_id))} "
                     f"(+{self._champion_metric() - self.best_single['metric']:.3f} over the best single).")
        elif self.rounds_since_improve + 1 >= self.cfg.stagnation_k:
            line += f" No promote for {self.rounds_since_improve + 1} rounds."
        return line

    # ------------------------------------------------------------ evaluate --
    def _evaluate(self, hyps, lane, seed=None):
        """Run one batch through the runner; records results, emits started/progress."""
        seed = self.cfg.seed if seed is None else seed
        specs, exp_ids = [], {}
        for h in hyps:
            sid = h.spec_id
            self.recipes.setdefault(sid, h.treatment)
            if seed in self.results.get(sid, {}):
                continue                                        # cached: never re-run a measured cell
            if any(s["spec_id"] == sid for s in specs):
                continue
            eid = self._next_id("e")
            exp_ids[sid] = eid
            specs.append({"spec_id": sid, "recipe": h.treatment, "seed": seed})
            self._emit("experiment_started", id=eid, hypothesis_id=h.id, lane=lane, spec=h.treatment,
                       runner=self.runner.label, sandbox_id=None, spec_id=sid, seed=seed,
                       cost_est=h.cost_est, text=h.text)
        results_by_id = {}
        lock = threading.Lock()

        def on_result(res):
            with lock:
                results_by_id[res["spec_id"]] = res
                self.evals_used += 1
                done = self.evals_used
            self._progress(live=True, evals_done=done)

        if specs:
            out = self.runner.evaluate_many(specs, on_result=on_result)
            for res in out or []:
                if res and res.get("spec_id") not in results_by_id:
                    with lock:
                        results_by_id[res["spec_id"]] = res
                        self.evals_used += 1
            for s in specs:                                     # a runner that dropped a spec = crash
                results_by_id.setdefault(s["spec_id"], {"spec_id": s["spec_id"], "metric": None, "std": None,
                                                        "cost_ms": 0, "params": None, "error": "runner returned no result",
                                                        "runtime": {"sandbox": self.runner.label, "sandbox_id": None, "ms": 0}})
        outs = []
        for h in hyps:
            sid = h.spec_id
            if sid in exp_ids:
                res = dict(results_by_id[sid])
                if res.get("metric") is not None and not (isinstance(res["metric"], float) and math.isnan(res["metric"])):
                    res["metric"] = float(res["metric"])
                else:
                    res["metric"] = None
                    res["error"] = res.get("error") or "no metric"
                res["_exp_id"] = exp_ids[sid]
                res["_seed"] = seed
                self.results.setdefault(sid, {})[seed] = res
            else:
                res = self.results[sid][seed]
            outs.append(res)
        return outs

    # ------------------------------------------------------------ gate --
    def _gate(self, h, res, champ_id, champ_before):
        g = {"gate": "discard", "reason": "", "delta": None, "champ_delta": None, "harm": None}
        if res.get("error"):
            g["gate"], g["reason"] = "crash", f"crash is not a scientific discard — {res['error']}"
            return g
        delta = self._paired_delta(h.spec_id, h.control_id) if h.control_id else None
        cd = self._paired_delta(h.spec_id, champ_id)
        g["delta"], g["champ_delta"] = delta, cd
        m = self._metric(h.spec_id)
        # superadditive harm: an ensemble, or a pair/triple of knobs, below its own best member
        if h.treatment.get("ensemble") in ("soft_vote", "stacking") or h.order >= 2:
            mem_id, mem_m = self._best_member(h.treatment)
            if mem_id and m <= mem_m - self.cfg.min_delta:
                g["harm"] = {"member": mem_id, "delta": m - mem_m}
                g["gate"] = "discard"
                g["reason"] = (f"superadditive harm: {m - mem_m:+.3f} below its best member "
                               f"{C.describe(self.recipes[mem_id])} — the combination is sealed, its members stay open")
                return g
        bar = self.cfg.min_delta
        seeds = len(self.results.get(h.spec_id, {}))
        paired = f" (paired mean over {seeds} fold seeds)" if seeds > 1 else ""
        if cd is not None and cd >= bar:
            g["gate"] = "promote"
            g["reason"] = f"{cd:+.4f} over the champion{paired}; bar +{bar:.3f}"
        elif delta is not None and delta >= bar:
            g["gate"] = "keep"
            g["reason"] = (f"{delta:+.4f} over its control {C.describe(h.control)} (bar +{bar:.3f}); "
                           f"{cd:+.4f} vs the champion — a better member, not a new champion")
        else:
            ref = cd if cd is not None else delta
            if ref is not None and abs(ref) < bar:
                g["reason"] = f"within measurement resolution: {ref:+.4f} is inside ±{bar:.3f}{paired}"
            elif ref is not None:
                g["reason"] = f"{ref:+.4f} vs control{paired} — below the bar"
            else:
                g["reason"] = "no control to compare against"
        return g

    def _apply(self, h, res, g, winner):
        gate = g["gate"]
        m = self._metric(h.spec_id)
        seed = h.seed
        exp = self.results[h.spec_id][seed]
        self._emit("experiment_result", id=exp["_exp_id"], metric=m, std=exp.get("std"),
                   delta_vs_control=g["delta"], cost_ms=exp.get("cost_ms"), gate=gate, reason=g["reason"],
                   hypothesis_id=h.id, spec_id=h.spec_id, spec=h.treatment, runtime=exp.get("runtime"),
                   delta_vs_champion=g["champ_delta"], params=exp.get("params"))
        self.ledger.append({"round": self.round, "lane": "confirm", "hypothesis_id": h.id, "kind": h.kind,
                            "family": h.family, "order": h.order, "knobs": h.knobs, "spec_id": h.spec_id,
                            "spec": h.treatment, "control_spec_id": h.control_id,
                            "control_metric": self._metric(h.control_id) if h.control_id else None,
                            "metric": m, "std": exp.get("std"), "delta": g["delta"],
                            "delta_vs_champion": g["champ_delta"], "gate": gate, "reason": g["reason"],
                            "cost_ms": exp.get("cost_ms"), "runtime": exp.get("runtime"),
                            "seeds": sorted(self.results[h.spec_id]), "harm": g.get("harm"),
                            "why": h.why, "falsifier": h.falsifier})
        self.window.append({"round": self.round, "hyp": h, "gate": gate, "delta": g["delta"],
                            "champ_delta": g["champ_delta"], "harm": g.get("harm")})
        if gate == "crash":
            return
        if g.get("harm"):
            self.sealed_combos[C.components(h.treatment)] = h.family
            self.harms.append({"spec_id": h.spec_id, "recipe": h.treatment, "metric": m, "family": h.family,
                               "member": g["harm"]["member"], "delta": g["harm"]["delta"], "round": self.round,
                               "text": h.text})
        # family evidence at the declared order
        st = self.families[h.family]
        st["evidence"].append({"spec": h.spec_id, "text": h.text, "delta": g["delta"], "metric": m,
                               "order": h.order, "gate": gate, "tokens": sorted(C.components(h.treatment)),
                               "round": self.round})
        if gate in ("promote", "keep"):
            st["passes"] += 1
        if gate == "keep" and not h.treatment.get("ensemble"):
            self.improved_members.append(h.treatment["model"])
        if gate == "promote" and winner is h:
            self.champion = {"spec_id": h.spec_id, "recipe": h.treatment, "metric": m,
                             "std": exp.get("std"), "seeds": sorted(self.results[h.spec_id]),
                             "promoted_round": self.round, "evals_used": self.evals_used}
            self.ledger.set_champion(self._champion_record())
            self._emit_champion(delta_vs_best_single=m - self.best_single["metric"])

    def _check_seal(self, fam):
        st = self.families[fam]
        if st["status"] != "open" or st["passes"]:
            return None
        ev = [e for e in st["evidence"] if e["gate"] == "discard"]
        orders = {min(e["order"], 3) for e in ev}
        if not {1, 2, 3} <= orders:
            return None
        st["status"] = "sealed"
        st["region"] = C.grid_region(fam)
        st["sealed_round"] = self.round
        evidence = [{"spec": e["spec"], "delta": e["delta"], "order": C.order_name(e["order"])} for e in ev]
        text = (f"Sealed family `{fam}` — isolated, pair, triple all below the bar "
                f"({', '.join(f'{e['delta']:+.3f}' for e in ev if e['delta'] is not None)}). "
                f"Won't recombine; that closes {st['region']} of {self.grid:,} grid cells.")
        self._emit("family_sealed", family=fam, evidence=evidence, text=text,
                   region=st["region"], mechanism=C.FAMILIES[fam].mechanism)
        return {"kind": "sealed", "title": text, "family": fam, "detail": C.FAMILIES[fam].mechanism}

    # ------------------------------------------------------------ critique --
    def _critique(self, cands, regrade=False):
        k = self.cfg.stagnation_k
        recent = [w for w in self.window if w["round"] > self.round - 1 - k and w["gate"] != "crash"]
        bar = self.cfg.min_delta
        ch = C.describe(self.champion["recipe"])
        grades = {}
        # a harm explains the stall only while its family is still open (a sealed family is closed, not stalled)
        harms = [w for w in recent if w.get("harm") and self.families[w["hyp"].family]["status"] == "open"]
        if harms:
            grades["superadditive_harm"] = max(harms, key=lambda w: (len(w["hyp"].treatment.get("members") or []),
                                                                     -w["harm"]["delta"]))
        keeps = [w for w in recent if w["gate"] == "keep"]
        if keeps and any("information_gain" in h.falsifies for h in cands):
            grades["information_gain"] = keeps[-1]
        taxed = [f for f in self.families if self.families[f]["status"] == "open" and self._is_taxed(f)]
        if taxed:
            grades["family_tax"] = taxed[0]
        small = [w for w in recent if w["champ_delta"] is not None and abs(w["champ_delta"]) < bar]
        if recent and len(small) * 2 >= len(recent):
            grades["measurement_resolution"] = small
        if not cands or (self.skipped and all(h.family in self.skipped for h in cands)):
            grades["frozen_axis"] = sorted(self.skipped)
        grades.setdefault("local_optimum", None)
        # never lead with a grade whose falsifiers are exhausted (that is what a regrade is for)
        order = [g for g in GRADE_PRIORITY if g in grades]
        if regrade and self.leading in order:
            order.remove(self.leading)
            order.append(self.leading)
        grade = order[0]
        for g in order:
            if g == "frozen_axis" or any(g in h.falsifies for h in cands):
                grade = g
                break
        else:
            grade = order[0]
        a, b, sep = self._explanations(grade, grades.get(grade), ch)
        fals = self._falsifiers(cands, grade)
        goals = self._information_goals(cands) if not fals else []
        if fals:
            nxt = f"{fals[0].text} — {fals[0].cost_est} ms est, 1 eval"
            tail = f"Cheapest falsifier: {nxt}."
        elif goals:
            g0 = goals[0]
            nxt = f"{g0.text} — characterize `{g0.family}` ({C.order_name(g0.order)}), {g0.cost_est} ms est, 1 eval"
            tail = (f"No unresolved experiment can falsify it. Cheapest information goal instead: {nxt} — "
                    f"a miss seals `{g0.family}` ({C.grid_region(g0.family)} grid cells).")
        else:
            nxt = "HOLD"
            tail = "No unresolved experiment can falsify it and nothing left carries an information goal — HOLD."
        ev = {"grade": grade, "explanations": [a, b], "separating_observation": sep, "next": nxt,
              "rounds_stalled": self.rounds_since_improve, "champion": ch, "champion_metric": self._champion_metric(),
              "candidates_left": len(cands)}
        self._emit("stagnation", **ev)
        self.critiques.append(ev)
        self._critiqued_round = self.round
        self._say(f"Stalled {self.rounds_since_improve} rounds. Grade: {grade}. Leading explanation: {a} "
                  f"Competing: {b} Separating observation: {sep} {tail}",
                  cards=[{"kind": "stagnation", "title": f"Why-critique · {grade}",
                          "rows": [{"ok": None, "mark": "A", "text": a}, {"ok": None, "mark": "B", "text": b},
                                   {"ok": None, "mark": "?", "text": sep},
                                   {"ok": None, "mark": "→", "text": nxt}]}])
        return grade

    def _explanations(self, grade, info, ch):
        bar = self.cfg.min_delta
        if grade == "superadditive_harm":
            w = info
            r = w["hyp"].treatment
            weakest = min(r.get("members") or [r["model"]], key=lambda m: (self._best_of_model(m)[1] or 0.0, m))
            return (f"{w['hyp'].text} hurt because its members disagree destructively — averaged probabilities "
                    f"drift toward the weaker, less calibrated learners (interaction).",
                    f"the damage is one member ({weakest}) alone; the rest of the composite is sound.",
                    f"the same composite without {weakest}: at or above its best member means one bad member, "
                    f"still below means interaction.")
        if grade == "information_gain":
            w = info
            m = w["hyp"].treatment["model"]
            return (f"{w['hyp'].text} improved {m} without beating {ch}; the gain is new information that "
                    f"should reopen composites containing {m}.",
                    f"{m}'s gain is already captured by the champion's transform; composites with it will tie.",
                    f"a composite containing the improved {m} scoring ≥ champion + {bar:.3f}.")
        if grade == "family_tax":
            fam = info
            ev = [e["delta"] for e in self.families[fam]["evidence"] if e["delta"] is not None]
            mean = sum(ev) / len(ev)
            return (f"`{fam}` carries a stable additive cost ({mean:+.3f} in every combination measured); "
                    f"no partner cancels it.",
                    f"`{fam}` only fails on the learners tried so far; a differently-biased learner would absorb it.",
                    f"`{fam}` on a learner from another family at or above that learner's own control.")
        if grade == "measurement_resolution":
            return (f"the last moves ({', '.join(f'{w['champ_delta']:+.3f}' for w in info[-4:])}) are smaller than the "
                    f"bar (±{bar:.3f}): the knobs tried are neutral at this resolution.",
                    f"the champion {ch} is at a real ceiling for single-lever moves; only a lever with a larger "
                    f"effect (a composite) can clear the bar.",
                    f"a composite over the champion clearing +{bar:.3f}: then the levers were too small, not the bar too wide.")
        if grade == "frozen_axis":
            return (f"steering froze the remaining levers ({', '.join(info) if info else 'no family left'}); "
                    f"nothing legal is left to launch.",
                    f"the catalog is exhausted under the seals; the search space is closed, not frozen.",
                    f"reopening a skipped family yields a legal, untested candidate — or it doesn't.")
        tested_ens = any(w["hyp"].treatment.get("ensemble") for w in self.window)
        if not tested_ens:
            return (f"{ch} sits in a local optimum of single-lever space: nearby transform and hyper knobs lose or tie.",
                    f"composites over the champion are untested — the gain is in combining learners, not tuning one.",
                    f"a soft vote or stack over the champion clearing +{bar:.3f}: local optimum falsified.")
        return (f"{ch} is a genuine optimum of this catalog: transforms, knobs and composites over it all lose or tie.",
                f"an open family still hides a pair/triple interaction that no isolated move could show.",
                f"characterizing the open families: a pair or triple clearing +{bar:.3f} reopens the search, "
                f"misses seal them and close the grid honestly.")

    def _hold(self, cands):
        sealed = self._sealed()
        reason = ("no candidate passes the information-gain gate" if cands else
                  "the catalog is exhausted under the seals and declined batches")
        if self._critiqued_round != self.round:
            ev = {"grade": self.leading or "local_optimum",
                  "explanations": [f"the champion {C.describe(self.champion['recipe'])} is the best this catalog can "
                                   f"support: every remaining cell is a sealed-family recombination, a closed bracket "
                                   f"or a comfort recipe.",
                                   "a family outside this catalog would move it — that is a literature cycle, not an eval."],
                  "separating_observation": "a new factor family (new mechanism) beating the champion by the bar",
                  "next": "HOLD", "rounds_stalled": self.rounds_since_improve, "candidates_left": len(cands)}
            self._emit("stagnation", **ev)
            self.critiques.append(ev)
        self._say(f"HOLD — {reason}. Not burning evals on recombination. "
                  f"Sealed: {', '.join(sealed) if sealed else 'none'}. "
                  f"Champion stands at {C.describe(self.champion['recipe'])} {_fmt(self._champion_metric())}.",
                  cards=[{"kind": "hold", "title": "HOLD — no falsifier left in the catalog",
                          "detail": reason}])

    # ------------------------------------------------------------ progress --
    def _progress(self, batch_metrics=None, live=False, evals_done=None):
        best = self._champion_metric()
        if batch_metrics is not None:
            vals = [m for m in batch_metrics if m is not None]
            if vals:
                self.history.append({"round": self.round, "best": best, "mean": sum(vals) / len(vals),
                                     "worst": min(vals), "top": max(vals), "n": len(vals)})
        self._emit("progress", evals_done=evals_done if evals_done is not None else self.evals_used,
                   evals_grid=self.grid, best_metric=best,
                   best_single_metric=self.best_single["metric"] if self.best_single else None,
                   history=list(self.history), round=self.round, live=live,
                   sealed=self._sealed(), evals_budget=self.cfg.max_evals,
                   rounds_since_improve=self.rounds_since_improve,
                   champion=C.describe(self.champion["recipe"]) if self.champion else None)

    def _champion_record(self):
        ch = self.champion
        return {"spec_id": ch["spec_id"], "spec": ch["recipe"], "text": C.describe(ch["recipe"]),
                "metric": self._metric(ch["spec_id"]), "std": ch.get("std"), "seeds": ch.get("seeds"),
                "delta_vs_best_single": (self._metric(ch["spec_id"]) - self.best_single["metric"]) if self.best_single else 0.0,
                "best_single": self.best_single, "evals_used": self.evals_used,
                "evals_grid_equivalent": self.grid, "promoted_round": ch.get("promoted_round"),
                "composite": C.is_composite(ch["recipe"])}

    def _emit_champion(self, delta_vs_best_single):
        ch = self.champion
        self._emit("champion", spec=ch["recipe"], metric=self._metric(ch["spec_id"]),
                   delta_vs_best_single=delta_vs_best_single, evals_used=self.evals_used,
                   evals_grid_equivalent=self.grid, spec_id=ch["spec_id"], text=C.describe(ch["recipe"]),
                   std=ch.get("std"), composite=C.is_composite(ch["recipe"]), round=self.round)

    def _champion_card(self):
        ch = self.champion
        m = self._metric(ch["spec_id"])
        return {"kind": "champion", "title": f"Champion → {C.describe(ch['recipe'])}  {_fmt(m)} · "
                f"{m - self.best_single['metric']:+.3f} over best single · {self.evals_used} evals "
                f"(grid would need {self.grid:,})", "spec_id": ch["spec_id"], "metric": m,
                "delta": m - self.best_single["metric"]}

    def _state(self):
        return {"round": self.round, "evals_used": self.evals_used, "champion": self._champion_record(),
                "families": {f: {"status": st["status"], "passes": st["passes"], "evidence": st["evidence"],
                                 "region": st["region"]} for f, st in self.families.items()},
                "sealed_combos": [{"tokens": sorted(c), "family": f} for c, f in self.sealed_combos.items()],
                "harms": self.harms,
                "rounds_since_improve": self.rounds_since_improve, "leading": self.leading,
                "critiques": self.critiques, "skipped": sorted(self.skipped), "focus": self.focus}

    def _finish(self, elapsed):
        ch = self.champion
        m = self._metric(ch["spec_id"]) if ch else None
        gain = (m - self.best_single["metric"]) if ch and self.best_single else None
        sealed = self._sealed()
        status = self.status if self.status in ("hold", "stopped", "budget", "error") else "done"
        verdict = ("composite champion" if ch and C.is_composite(ch["recipe"]) and gain is not None and gain >= self.cfg.min_delta
                   else "HOLD — no composite cleared the bar; the best single stands" if ch and not C.is_composite(ch["recipe"])
                   else "champion")
        why_end = {"hold": "no falsifier left", "stopped": "stopped by you",
                   "budget": f"budget of {self.cfg.max_evals} evals reached", "done": "finished"}.get(status, status)
        text = (f"Done ({why_end}) · {self.evals_used} evals of a {self.grid:,}-cell grid · "
                f"{verdict}: {C.describe(ch['recipe'])} {_fmt(m)} ({_sd(gain)} over best single "
                f"{self.best_single['recipe']['model']} {_fmt(self.best_single['metric'])}) · "
                f"sealed: {', '.join(sealed) if sealed else 'none'} · "
                f"{len(self.harms)} harmful composite{'s' if len(self.harms) != 1 else ''} · "
                f"{elapsed:.1f}s.")
        stats = {"status": status, "verdict": verdict, "champion": self._champion_record() if ch else None,
                 "best_single": self.best_single, "evals_used": self.evals_used, "evals_grid": self.grid,
                 "grid_pruned": sum(st["region"] or 0 for st in self.families.values() if st["status"] == "sealed"),
                 "sealed": sealed, "harms": self.harms, "rounds": self.round, "elapsed_s": round(elapsed, 2),
                 "critiques": len(self.critiques), "min_delta": self.cfg.min_delta,
                 "composite_beats_single": bool(ch and C.is_composite(ch["recipe"]) and gain is not None and gain >= self.cfg.min_delta)}
        self.ledger.save_state(self._state())
        self._say(text, cards=[self._champion_card()] if ch else None)
        self._llm_insight(text, stats)
        self._emit("done", summary=text, stats=stats)
        self.status = status
        return stats


__all__ = ["ResearchLoop", "Hypothesis", "InlineRunner"]

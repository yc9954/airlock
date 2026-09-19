"""The airlock itself: generate wide → run+measure → prune → diversify → (human).

Two entry points share the machinery:

  * run()           one pass — generate, measure, prune, diversify.
  * run_evolution() many passes — each generation is bred from the last one's
                    elites, the defect rate decays, and because fitness is
                    measured continuously the best/mean rises generation over
                    generation. That climbing curve is the product's own proof
                    that selection-on-real-measurement actually improves output.

The human is not in this file. The pipeline hands over a handful of measured,
mutually-distinct finalists and stops; `select()` is the last door, opened by a
person in the UI.
"""
import time
from statistics import mean as _mean

from .config import Config
from .generate import get_generator
from .sandbox import get_sandbox
from .measure import measure
from .diversity import select_diverse
from .deploy import get_deployer


class Pipeline:
    def __init__(self, cfg: Config, base_url: str):
        self.cfg = cfg
        self.base_url = base_url
        self.generator = get_generator(cfg)
        self.sandbox = get_sandbox(cfg)
        self.deployer = get_deployer(cfg)
        self.runs = {}

    # ---- shared: build + serve + measure one variant ----
    def _evaluate(self, run_id, v, sink, emit):
        served = self.sandbox.build_and_serve(run_id, v, self.base_url)
        v["url"] = served["url"]
        v["runtime"] = served["runtime"]
        signals, fitness, verdict, reasons = measure(v["html"])
        if not v["runtime"].get("served"):
            verdict, fitness = "fail", 0.0
            reasons = reasons + ["hard:served"]
        v.update({"signals": signals, "fitness": fitness, "verdict": verdict, "reasons": reasons})
        sink.append(v)
        emit({"type": "measured", "id": v["id"], "gen": v.get("gen", 0), "url": v["url"],
              "meta": v["meta"], "signals": signals, "fitness": fitness,
              "verdict": verdict, "reasons": reasons, "runtime": v["runtime"]})
        if self.cfg.pace_ms:
            time.sleep(self.cfg.pace_ms / 1000.0)

    # ---- single pass ----
    def run(self, run_id, prompt, emit):
        n, k = self.cfg.n_generate, self.cfg.k_finalists
        emit({"type": "run_started", "run_id": run_id, "prompt": prompt, "n": n, "k": k,
              "generations": 1, "providers": self.cfg.provider_labels()})
        variants = []
        self.generator.generate(prompt, n, emit=lambda v: self._evaluate(run_id, v, variants, emit))
        return self._finish(run_id, prompt, variants, variants, [], emit)

    # ---- evolutionary loop ----
    def run_evolution(self, run_id, prompt, emit):
        n, k = self.cfg.n_generate, self.cfg.k_finalists
        G, E = max(1, self.cfg.generations), self.cfg.elite_count
        emit({"type": "run_started", "run_id": run_id, "prompt": prompt, "n": n, "k": k,
              "generations": G, "providers": self.cfg.provider_labels()})

        all_variants, elites, history, last_survivors = [], None, [], []
        for gen in range(G):
            emit({"type": "generation_start", "gen": gen, "n": n})
            pop = []
            self.generator.generate(prompt, n, gen=gen, elites=elites,
                                    emit=lambda v: self._evaluate(run_id, v, pop, emit))
            all_variants += pop
            survivors = [v for v in pop if v["verdict"] == "pass"] or pop
            survivors.sort(key=lambda v: -v["fitness"])
            fits = [v["fitness"] for v in pop]
            best, worst, avg = round(max(fits), 1), round(min(fits), 1), round(_mean(fits), 1)
            history.append({"gen": gen, "best": best, "mean": avg, "worst": worst})
            elites = survivors[:E]
            last_survivors = [v for v in pop if v["verdict"] == "pass"]
            emit({"type": "generation", "gen": gen, "best": best, "mean": avg, "worst": worst,
                  "pop": len(pop), "survivors": len(last_survivors),
                  "best_id": survivors[0]["id"] if survivors else None})
            if self.cfg.gen_pace_ms and gen < G - 1:
                time.sleep(self.cfg.gen_pace_ms / 1000.0)

        return self._finish(run_id, prompt, all_variants,
                            last_survivors or all_variants, history, emit)

    def _finish(self, run_id, prompt, all_variants, pool, history, emit):
        survivors = [v for v in pool if v["verdict"] == "pass"] or pool
        culled = [v["id"] for v in all_variants if v["verdict"] == "fail"]
        emit({"type": "pruned", "kept": [v["id"] for v in survivors], "culled": culled,
              "kept_n": len(survivors), "culled_n": len(culled)})
        finalists = select_diverse(survivors, self.cfg.k_finalists)
        emit({"type": "finalists", "ids": [v["id"] for v in finalists],
              "detail": [{"id": v["id"], "fitness": v["fitness"], "meta": v["meta"],
                          "url": v["url"], "gen": v.get("gen", 0)} for v in finalists]})
        self.runs[run_id] = {"prompt": prompt, "variants": {v["id"]: v for v in all_variants},
                             "finalists": [v["id"] for v in finalists], "history": history}
        emit({"type": "done", "generated": len(all_variants),
              "survived": len(survivors), "finalists": len(finalists), "history": history})
        return finalists

    def select(self, run_id, variant_id):
        run = self.runs.get(run_id)
        if not run or variant_id not in run["variants"]:
            raise KeyError("unknown run or variant")
        champ = run["variants"][variant_id]
        result = self.deployer.deploy(run_id, champ, self.base_url)
        result.update({"variant_id": variant_id, "fitness": champ["fitness"], "meta": champ["meta"]})
        return result

    def teardown(self):
        try:
            self.sandbox.teardown()
        except Exception:
            pass

"""Every constant that decides the science, in one place.

Nothing in `evaluate.py`, `catalog.py` or `loop.py` hard-codes a dataset, a fold
count, a seed or a threshold: they read a `ResearchConfig`. Swap the dataset or
the bar here and the whole engine follows.

Why these defaults (measured on 2026-09-19, 5-fold, seed 7 — see tests):

    dataset = breast_cancer_noisy
        load_breast_cancer (569 x 30, real nonlinear structure) plus 20 engineered
        Gaussian noise columns whose scales are log-uniform over ~[0.1, 400]. The
        noise gives feature selection / scaling something to do and pulls the
        unscaled learners apart: raw kNN 0.896, raw MLP 0.856, raw linear-SVM 0.73,
        raw logistic 0.944, raw RF 0.963 (the best single). standardize+logistic
        then lands at 0.975 (+0.012 over the best single) and soft-vote / stack
        (logistic, rf) at 0.979 — composites have room to win, and several
        composites lose to their own members (vote(nb, tree) 0.926 < nb 0.935;
        pca costs 0.02-0.05 in every combination), so harm is demonstrable too.

    min_delta = 0.003
        One sample of 569 is 0.00176 accuracy; the bar is ~2 samples on paired
        folds. Sub-bar positive moves are `repeat`ed on a second fold seed and
        gated on the paired mean, so resolution is handled by the protocol, not
        by a wider bar.
"""
import os
from dataclasses import dataclass, field, asdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STAGNATION_GRADES = ("local_optimum", "family_tax", "superadditive_harm",
                     "measurement_resolution", "frozen_axis", "information_gain")
GATES = ("promote", "keep", "discard", "explore_only", "repeat", "crash")
LANES = ("explore", "confirm", "repair")


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class ResearchConfig:
    # ---- the task ----
    dataset: str = field(default_factory=lambda: os.environ.get("AIRLOCK_DATASET", "breast_cancer_noisy"))
    metric: str = "accuracy"                 # stratified k-fold CV accuracy
    folds: int = field(default_factory=lambda: _env_int("AIRLOCK_FOLDS", 5))
    seed: int = field(default_factory=lambda: _env_int("AIRLOCK_SEED", 7))
    noise_features: int = 20                 # engineered noise columns (breast_cancer_noisy)
    noise_seed: int = 1234                   # RNG for the engineered columns (fixed, not `seed`)
    label_flip: float = 0.0                  # fraction of labels flipped deterministically

    # ---- the bar ----
    min_delta: float = field(default_factory=lambda: _env_float("AIRLOCK_MIN_DELTA", 0.003))
    max_repeats: int = 1                     # sub-bar positive moves get one paired repeat
    stagnation_k: int = 3                    # rounds without a promote before a why-critique

    # ---- the budget ----
    max_evals: int = field(default_factory=lambda: _env_int("AIRLOCK_MAX_EVALS", 60))
    batch_size: int = 8                      # confirm batch width (parallel sandboxes)
    top_singles: int = 3                     # how many explore winners get their own transform screen

    # ---- asks (pause for approval before an expensive batch) ----
    ask_threshold_local: int = 9             # ask when a local batch is bigger than this
    ask_threshold_daytona: int = 4           # ...or a Daytona batch is bigger than this
    ask_timeout_s: float = 30.0              # then take `ask_default`
    ask_default: str = "run"

    # ---- where the ledger lives ----
    runs_dir: str = field(default_factory=lambda: os.environ.get(
        "AIRLOCK_LABS", os.path.join(ROOT, "runs", "labs")))

    # ---- demo pacing (0 = as fast as possible) ----
    pace_ms: int = field(default_factory=lambda: _env_int("AIRLOCK_RESEARCH_PACE_MS", 0))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_app(cls, app_cfg=None, **overrides):
        """Build from the server-level `airlock.config.Config` (or None). Only the
        keys ResearchConfig knows are taken; everything else is ignored."""
        rc = cls()
        if app_cfg is not None:
            for k in ("dataset", "folds", "seed", "min_delta", "max_evals", "runs_dir"):
                v = getattr(app_cfg, k, None)
                if v is not None:
                    setattr(rc, k, v)
        for k, v in overrides.items():
            if v is not None and hasattr(rc, k):
                setattr(rc, k, v)
        return rc


__all__ = ["ResearchConfig", "STAGNATION_GRADES", "GATES", "LANES", "ROOT"]

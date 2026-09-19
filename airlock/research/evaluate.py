"""The pure evaluator: recipe + seed → one primary metric.

    evaluate(recipe, seed) -> {'metric', 'std', 'params', 'cost_ms'}

Builds a real scikit-learn Pipeline from a catalog recipe (transform chain →
model, or → VotingClassifier / StackingClassifier / BaggingClassifier over the
members), scores it with stratified k-fold CV accuracy on the bundled dataset,
and returns the mean ± std, a parameter count from the first fold's fitted
estimator, and the wall time. Deterministic: every RNG (fold split, forest,
boosting, MLP, PCA, quantile map, bootstrap) is seeded from `seed`; the
engineered noise columns are seeded from `cfg.noise_seed`, not from `seed`, so a
second-seed repeat re-splits the *same* data.

Runners call this with two arguments — the dataset, fold count and everything
else come from `ResearchConfig()` (env-overridable) unless `cfg` is passed.
"""
import time
import warnings

import numpy as np

from .config import ResearchConfig
from . import catalog as C

_DATA = {}          # (dataset, noise_features, noise_seed, label_flip) -> (X, y, info)
_DEFAULT_CFG = None


def default_config():
    global _DEFAULT_CFG
    if _DEFAULT_CFG is None:
        _DEFAULT_CFG = ResearchConfig()
    return _DEFAULT_CFG


# ------------------------------------------------------------------ dataset --
def load_dataset(cfg=None):
    """(X, y, info) for cfg.dataset. Cached per process."""
    cfg = cfg or default_config()
    key = (cfg.dataset, int(cfg.noise_features), int(cfg.noise_seed), float(cfg.label_flip))
    if key in _DATA:
        return _DATA[key]
    from sklearn import datasets
    rng = np.random.RandomState(int(cfg.noise_seed))
    name = cfg.dataset
    if name in ("breast_cancer", "breast_cancer_noisy"):
        X, y = datasets.load_breast_cancer(return_X_y=True)
        X = np.asarray(X, dtype=float)
        n_noise = int(cfg.noise_features) if name == "breast_cancer_noisy" else 0
        if n_noise:
            # noise columns on wildly different scales (log-uniform ~[0.1, 400]):
            # harmless to trees, poison for unscaled distance/gradient learners.
            scales = np.exp(rng.uniform(-2, 6, size=n_noise))
            X = np.hstack([X, rng.normal(size=(X.shape[0], n_noise)) * scales])
        desc = "breast_cancer" + (f" + {n_noise} noise columns" if n_noise else "")
    elif name == "synthetic":
        X, y = datasets.make_classification(
            n_samples=1500, n_features=30, n_informative=8, n_redundant=4,
            n_clusters_per_class=3, flip_y=0.03, class_sep=0.9,
            random_state=int(cfg.noise_seed))
        desc = "make_classification(1500x30, 3 clusters/class)"
    else:
        raise ValueError(f"unknown dataset {name!r}")
    y = np.asarray(y).astype(int)
    if cfg.label_flip:
        k = int(round(float(cfg.label_flip) * len(y)))
        idx = rng.choice(len(y), k, replace=False)
        y = y.copy()
        y[idx] = 1 - y[idx]
        desc += f", {k} labels flipped"
    info = {"name": name, "n": int(X.shape[0]), "d": int(X.shape[1]),
            "classes": int(len(np.unique(y))), "desc": desc}
    _DATA[key] = (X, y, info)
    return _DATA[key]


# ----------------------------------------------------------------- builders --
def _transform(name, seed):
    from sklearn.preprocessing import StandardScaler, PolynomialFeatures, QuantileTransformer
    from sklearn.decomposition import PCA
    from sklearn.feature_selection import SelectKBest, f_classif
    if name == "standardize":
        return StandardScaler()
    if name == "quantile":
        return QuantileTransformer(n_quantiles=100, output_distribution="normal", random_state=seed)
    if name == "polynomial":
        return PolynomialFeatures(degree=2, include_bias=False)
    if name == "pca":
        return PCA(n_components=10, random_state=seed)
    if name == "kbest":
        return SelectKBest(f_classif, k=15)
    raise ValueError(f"unknown transform {name!r}")


def _model(name, seed, hyper=None):
    from sklearn.linear_model import LogisticRegression
    from sklearn.neighbors import KNeighborsClassifier
    from sklearn.tree import DecisionTreeClassifier
    from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier
    from sklearn.naive_bayes import GaussianNB
    from sklearn.svm import SVC
    from sklearn.neural_network import MLPClassifier
    h = dict(hyper or {})
    if name == "logistic":
        kw = dict(max_iter=2000, C=1.0)
        kw.update(h)
        if kw.get("penalty") == "l1":
            kw["solver"] = "liblinear"
        return LogisticRegression(**kw)
    if name == "linsvm":
        kw = dict(kernel="linear", probability=True, random_state=seed, max_iter=20000, C=1.0)
        kw.update(h)
        return SVC(**kw)
    if name == "knn":
        kw = dict(n_neighbors=7)
        kw.update(h)
        return KNeighborsClassifier(**kw)
    if name == "tree":
        kw = dict(max_depth=5, random_state=seed)
        kw.update(h)
        return DecisionTreeClassifier(**kw)
    if name == "rf":
        kw = dict(n_estimators=60, random_state=seed, n_jobs=1)
        kw.update(h)
        return RandomForestClassifier(**kw)
    if name == "gb":
        kw = dict(max_iter=60, max_depth=3, learning_rate=0.1, random_state=seed)
        kw.update(h)
        return HistGradientBoostingClassifier(**kw)
    if name == "nb":
        return GaussianNB(**h)
    if name == "mlp":
        kw = dict(hidden_layer_sizes=(32,), max_iter=400, random_state=seed)
        kw.update(h)
        if isinstance(kw["hidden_layer_sizes"], (int, float)):
            kw["hidden_layer_sizes"] = (int(kw["hidden_layer_sizes"]),)
        elif isinstance(kw["hidden_layer_sizes"], list):
            kw["hidden_layer_sizes"] = tuple(kw["hidden_layer_sizes"])
        return MLPClassifier(**kw)
    raise ValueError(f"unknown model {name!r}")


def build_pipeline(recipe, seed):
    """A fresh, unfitted sklearn Pipeline for the recipe. Raises on illegal recipes."""
    from sklearn.pipeline import Pipeline
    from sklearn.ensemble import VotingClassifier, StackingClassifier, BaggingClassifier
    ok, why = C.check_legal(recipe)
    if not ok:
        raise ValueError(f"illegal recipe: {why}")
    steps = [(p, _transform(p, seed)) for p in C.transform_parts(recipe)]
    ens, m, mem, hyper = recipe.get("ensemble"), recipe["model"], recipe.get("members") or [], recipe.get("hyper")
    if ens == "soft_vote":
        est = [(x, _model(x, seed, hyper if x == m else None)) for x in mem]
        core = VotingClassifier(est, voting="soft")
    elif ens == "stacking":
        est = [(x, _model(x, seed)) for x in mem]
        core = StackingClassifier(est, final_estimator=_model(m, seed, hyper), cv=3, stack_method="auto")
    elif ens == "bagging":
        core = BaggingClassifier(_model(m, seed, hyper), n_estimators=10, random_state=seed)
    else:
        core = _model(m, seed, hyper)
    steps.append(("model", core))
    return Pipeline(steps)


# ---------------------------------------------------------- parameter count --
def count_params(est):
    """A rough learned-parameter count of a fitted estimator (recursive)."""
    n = 0
    seen = set()

    def walk(e):
        nonlocal n
        if e is None or id(e) in seen:
            return
        seen.add(id(e))
        for attr in ("coef_", "intercept_", "theta_", "var_", "class_prior_", "components_",
                     "mean_", "scale_", "support_vectors_", "dual_coef_", "quantiles_"):
            v = getattr(e, attr, None)
            if v is not None and hasattr(v, "size"):
                n += int(v.size)
        for attr in ("coefs_", "intercepts_"):
            v = getattr(e, attr, None)
            if v:
                n += int(sum(getattr(a, "size", 0) for a in v))
        tree = getattr(e, "tree_", None)
        if tree is not None:
            n += int(tree.node_count) * 2
        if hasattr(e, "_fit_X"):                       # kNN stores the training set
            n += int(getattr(e, "_fit_X").size)
        for attr in ("estimators_", "final_estimator_", "steps", "named_steps"):
            v = getattr(e, attr, None)
            if v is None:
                continue
            if isinstance(v, dict):
                v = list(v.values())
            if isinstance(v, (list, tuple)):
                for item in v:
                    walk(item[1] if isinstance(item, tuple) else item)
            elif hasattr(v, "__iter__") and not hasattr(v, "fit"):
                for item in v:
                    walk(item)
            else:
                walk(v)
        pred = getattr(e, "_predictors", None)         # HistGradientBoosting
        if pred:
            n += int(sum(p.nodes.shape[0] * 2 for it in pred for p in it))
    walk(est)
    return n


# ----------------------------------------------------------------- evaluate --
def _single_thread():
    """One BLAS/OpenMP thread per evaluation: the runner supplies the parallelism
    (one process per candidate), and HistGradientBoosting on 569 rows is 4x
    slower with 10 spinning OpenMP threads than with one. Metrics are identical."""
    try:
        from threadpoolctl import threadpool_limits
        return threadpool_limits(limits=1)
    except Exception:                                  # threadpoolctl missing
        import contextlib
        return contextlib.nullcontext()


def evaluate(recipe, seed, cfg=None):
    """recipe + seed → {'metric', 'std', 'params', 'cost_ms'} (accuracy, k-fold)."""
    from sklearn.model_selection import StratifiedKFold, cross_validate
    cfg = cfg or default_config()
    X, y, _ = load_dataset(cfg)
    pipe = build_pipeline(recipe, int(seed))
    skf = StratifiedKFold(n_splits=int(cfg.folds), shuffle=True, random_state=int(seed))
    t0 = time.time()
    with warnings.catch_warnings(), _single_thread():
        warnings.simplefilter("ignore")
        res = cross_validate(pipe, X, y, cv=skf, scoring=cfg.metric, n_jobs=1,
                             return_estimator=True, error_score="raise")
    scores = np.asarray(res["test_score"], dtype=float)
    cost_ms = int(round((time.time() - t0) * 1000))
    try:
        params = int(count_params(res["estimator"][0]))
    except Exception:
        params = None
    return {"metric": float(scores.mean()), "std": float(scores.std()),
            "params": params, "cost_ms": cost_ms}


def dataset_info(cfg=None):
    return load_dataset(cfg)[2]


__all__ = ["evaluate", "build_pipeline", "load_dataset", "dataset_info", "count_params",
           "default_config"]

"""The candidate catalog: factors, families, recipes, legality, cost, grid size.

Vocabulary (mirrors the autoresearch protocol):

    factor    one component: a transform, a model, an ensemble method, or one
              discrete hyper-parameter level of one model.
    group     the four UI groups: transform | model | ensemble | hyper.
    family    a *knob set of one mechanism* — the unit that gets characterized
              and sealed (`polynomial`, `reduction`, `voting`, `hyper.logistic`…).
              Finer than a group: sealing "all transforms" would be nonsense,
              sealing "polynomial features" is a finding.
    recipe    a composition  transform × model × ensemble × hyper. Transforms
              chain with '>' ("standardize>pca"). Ensembles take `members`.
    order     of a treatment vs its control = how many knobs it turns on
              (1 isolated, 2 pair, 3+ triple). Family evidence is charged at
              that order; a family whose isolated, pair and triple evidence
              all miss the bar is sealed and never recombined.

Recipe dict — the wire format shared with the runners:

    {'transform': 'standardize', 'model': 'logistic', 'ensemble': None,
     'hyper': {'C': 0.1}, 'members': None}
    {'transform': 'standardize', 'model': 'logistic', 'ensemble': 'soft_vote',
     'hyper': {}, 'members': ['logistic', 'rf']}          # model ∈ members (the lead)
    {'transform': 'none', 'model': 'logistic', 'ensemble': 'stacking',
     'hyper': {}, 'members': ['knn', 'rf']}               # model = the meta learner
    {'transform': 'none', 'model': 'tree', 'ensemble': 'bagging',
     'hyper': {}, 'members': None}                        # model = the bagged base
"""
from dataclasses import dataclass, field
from itertools import combinations

GROUPS = ("transform", "model", "ensemble", "hyper")
MAX_CHAIN = 3            # transforms chained per recipe
MAX_MEMBERS = 4          # voting / stacking members
MIN_MEMBERS = 2


@dataclass(frozen=True)
class Factor:
    name: str
    group: str
    family: str
    cost: int                 # estimated ms for 5-fold CV on the bundled dataset (measured 2026-09-19)
    desc: str = ""
    model: str = None         # hyper factors: the model they tune
    param: str = None
    value: object = None


@dataclass
class Family:
    name: str
    group: str
    mechanism: str
    factors: list = field(default_factory=list)


# ------------------------------------------------------------------ factors --
_F = []


def _add(*a, **kw):
    _F.append(Factor(*a, **kw))


# transform group  (cost = transform itself; polynomial also multiplies the model cost)
_add("none",        "transform", "identity",   0,   "raw features")
_add("standardize", "transform", "scaling",    10,  "zero-mean / unit-variance columns")
_add("quantile",    "transform", "scaling",    60,  "map each column to a normal via its quantiles")
_add("polynomial",  "transform", "polynomial", 300, "degree-2 features (pairwise products)")
_add("pca",         "transform", "reduction",  30,  "project onto 10 principal components")
_add("kbest",       "transform", "reduction",  10,  "keep the 15 columns with the highest F-score")
# model group
_add("logistic",    "model", "linear",    100, "logistic regression (lbfgs)")
_add("linsvm",      "model", "linear",    250, "linear-kernel SVM with probabilities")
_add("knn",         "model", "neighbors", 60,  "k-nearest neighbours, k=7")
_add("tree",        "model", "trees",     30,  "decision tree, depth 5")
_add("rf",          "model", "trees",     320, "random forest, 60 trees")
_add("gb",          "model", "trees",     260, "histogram gradient boosting, 60 iters")
_add("nb",          "model", "bayes",     10,  "gaussian naive bayes")
_add("mlp",         "model", "neural",    650, "one hidden layer of 32")
# ensemble group  (cost applied on top of the members, see cost_estimate)
_add("soft_vote",   "ensemble", "voting",   20,  "average member probabilities")
_add("stacking",    "ensemble", "stacking", 60,  "members' out-of-fold outputs → meta learner")
_add("bagging",     "ensemble", "bagging",  40,  "10 bootstrap copies of the base model")
# hyper group: one family per model, one factor per discrete level
_H = [
    ("logistic", "C", 0.1, 100), ("logistic", "C", 10, 120),
    ("logistic", "class_weight", "balanced", 100), ("logistic", "penalty", "l1", 150),
    ("knn", "n_neighbors", 3, 60), ("knn", "n_neighbors", 15, 60), ("knn", "weights", "distance", 60),
    ("tree", "max_depth", 3, 25), ("tree", "max_depth", 8, 35),
    ("rf", "n_estimators", 200, 900), ("rf", "max_depth", 6, 280),
    ("gb", "max_iter", 200, 700), ("gb", "learning_rate", 0.03, 260),
    ("linsvm", "C", 0.1, 250), ("linsvm", "C", 10, 400),
    ("mlp", "alpha", 0.01, 650), ("mlp", "hidden_layer_sizes", 64, 900),
    ("nb", "var_smoothing", 0.001, 10),
]
for _m, _p, _v, _c in _H:
    _add(f"{_m}.{_p}={_v}", "hyper", f"hyper.{_m}", _c, f"{_m} with {_p}={_v}", model=_m, param=_p, value=_v)

FACTORS = {f.name: f for f in _F}
TRANSFORMS = [f.name for f in _F if f.group == "transform"]
MODELS = [f.name for f in _F if f.group == "model"]
ENSEMBLES = [f.name for f in _F if f.group == "ensemble"]
HYPERS = [f.name for f in _F if f.group == "hyper"]
SCALERS = ("standardize", "quantile")

_MECH = {
    "identity": "no preprocessing",
    "scaling": "put columns on a common scale so distance- and gradient-based learners see every feature",
    "polynomial": "expose pairwise interactions to linear learners",
    "reduction": "drop or fold noise columns before the learner",
    "linear": "a single linear decision boundary",
    "neighbors": "local majority vote in feature space",
    "trees": "axis-aligned splits, scale-invariant",
    "bayes": "per-class gaussian likelihoods",
    "neural": "one nonlinear hidden layer",
    "voting": "average calibrated probabilities of diverse members",
    "stacking": "let a meta learner weight members by out-of-fold evidence",
    "bagging": "variance reduction by bootstrap resampling",
}
FAMILIES = {}
for _f in _F:
    fam = FAMILIES.get(_f.family)
    if fam is None:
        mech = _MECH.get(_f.family) or (f"discrete levels of {_f.model}'s knobs" if _f.model else "")
        fam = FAMILIES[_f.family] = Family(_f.family, _f.group, mech)
    fam.factors.append(_f.name)


def family_of(factor):
    return FACTORS[factor].family


def factors_in_family(family):
    return list(FAMILIES[family].factors)


def hyper_factors_for(model):
    return [f for f in HYPERS if FACTORS[f].model == model]


def hyper_factor_name(model, param, value):
    return f"{model}.{param}={value}"


def hyper_levels(model):
    """{param: [values]} for one model."""
    out = {}
    for f in hyper_factors_for(model):
        out.setdefault(FACTORS[f].param, []).append(FACTORS[f].value)
    return out


# ------------------------------------------------------------------ recipes --
def make_recipe(transform="none", model="logistic", ensemble=None, hyper=None, members=None):
    """Normalise into the wire format (sorted members, {} hyper, None members)."""
    if isinstance(transform, (list, tuple)):
        transform = ">".join(t for t in transform if t and t != "none") or "none"
    r = {"transform": transform or "none", "model": model,
         "ensemble": ensemble or None, "hyper": dict(hyper or {}),
         "members": sorted(members) if members else None}
    if r["ensemble"] in (None, "bagging"):
        r["members"] = None
    return r


def transform_parts(recipe):
    t = recipe.get("transform") or "none"
    return [p for p in t.split(">") if p and p != "none"]


def recipe_id(recipe):
    """A stable, human-legible id: 'standardize|soft_vote(logistic,rf)|C=0.1'."""
    r = recipe
    t = ">".join(transform_parts(r)) or "none"
    ens, m, mem = r.get("ensemble"), r["model"], r.get("members") or []
    if ens == "soft_vote":
        core = f"soft_vote({','.join(sorted(mem))})"
    elif ens == "stacking":
        core = f"stacking({','.join(sorted(mem))}->{m})"
    elif ens == "bagging":
        core = f"bagging({m})"
    else:
        core = m
    h = ",".join(f"{k}={v}" for k, v in sorted((r.get("hyper") or {}).items()))
    return f"{t}|{core}" + (f"|{h}" if h else "")


def describe(recipe):
    """The chat's voice: 'standardize + soft-vote(logistic, rf)'."""
    r = recipe
    parts = transform_parts(r)
    ens, m, mem = r.get("ensemble"), r["model"], r.get("members") or []
    if ens == "soft_vote":
        core = f"soft-vote({', '.join(sorted(mem))})"
    elif ens == "stacking":
        core = f"stack({', '.join(sorted(mem))} → {m})"
    elif ens == "bagging":
        core = f"bag({m})"
    else:
        core = m
    h = r.get("hyper") or {}
    hs = f" ({', '.join(f'{k}={v}' for k, v in sorted(h.items()))})" if h else ""
    t = ">".join("polynomial(2)" if p == "polynomial" else p for p in parts)
    return (f"{t} + " if t else "") + core + hs


def components(recipe):
    """The knob tokens a recipe carries. Members share the 'model:' namespace so a
    single model is a proper sub-recipe of any ensemble containing it."""
    r = recipe
    toks = set(transform_parts(r))
    ens, m = r.get("ensemble"), r["model"]
    if ens == "soft_vote":
        toks.add("soft_vote")
        toks.update(f"model:{x}" for x in r.get("members") or [])
    elif ens == "stacking":
        toks.add("stacking")
        toks.update(f"model:{x}" for x in r.get("members") or [])
        toks.add(f"meta:{m}")
    elif ens == "bagging":
        toks.add("bagging")
        toks.add(f"model:{m}")
    else:
        toks.add(f"model:{m}")
    for k, v in (r.get("hyper") or {}).items():
        toks.add(f"hyper:{hyper_factor_name(m, k, v)}")
    return frozenset(toks)


def factors_of(recipe):
    """Catalog factor names used by a recipe (drops the 'model:'/'meta:'/'hyper:' prefixes)."""
    out = set()
    for tok in components(recipe):
        if ":" in tok:
            out.add(tok.split(":", 1)[1])
        else:
            out.add(tok)
    return {f for f in out if f in FACTORS}


def families_of(recipe):
    return {FACTORS[f].family for f in factors_of(recipe)}


def knobs_changed(control, treatment):
    """Tokens the treatment turns on that the control does not (its order = len)."""
    return sorted(components(treatment) - components(control))


def treatment_order(control, treatment):
    """How many *knobs* a treatment turns on versus its control. The ensemble
    method itself and a stacking meta learner are the frame, not knobs: the
    partners are the knobs (a 2-member vote is isolated, 3 is a pair, 4 a triple)."""
    knobs = knobs_changed(control, treatment) if control else sorted(components(treatment))
    n = sum(1 for k in knobs if k not in ENSEMBLES and not k.startswith("meta:"))
    return max(1, n)


def order_name(order):
    return {1: "isolated", 2: "pair"}.get(order, "triple") if order >= 1 else "none"


def is_composite(recipe):
    return len(components(recipe)) > 1


def composite_order(recipe):
    """Number of components beyond the lead model (0 for a bare single)."""
    return max(0, len(components(recipe)) - 1)


def is_sub_recipe(small, big):
    a, b = components(small), components(big)
    return a < b and len(a) >= 1


# ----------------------------------------------------------------- legality --
def check_legal(recipe, sealed_families=(), sealed_combos=()):
    """(ok, reason). Structural rules first, then the research rules:
    sealed families forbid any recombination, sealed combinations (superadditive
    harm) forbid the exact set and any superset of it."""
    r = recipe
    if not isinstance(r, dict):
        return False, "recipe must be a dict"
    m = r.get("model")
    if m not in MODELS:
        return False, f"unknown model {m!r}"
    parts = transform_parts(r)
    for p in parts:
        if p not in TRANSFORMS:
            return False, f"unknown transform {p!r}"
    if len(parts) != len(set(parts)):
        return False, "duplicate transform in chain"
    if len(parts) > MAX_CHAIN:
        return False, f"more than {MAX_CHAIN} transforms chained"
    if sum(p in SCALERS for p in parts) > 1:
        return False, "two scalers in one chain"
    if any(p in SCALERS for p in parts) and parts[0] not in SCALERS:
        return False, "a scaler must come first in the chain"
    ens, mem = r.get("ensemble"), r.get("members")
    if ens is not None and ens not in ENSEMBLES:
        return False, f"unknown ensemble {ens!r}"
    if ens in ("soft_vote", "stacking"):
        if not mem or len(mem) < MIN_MEMBERS:
            return False, f"{ens} needs at least {MIN_MEMBERS} members"
        if len(mem) > MAX_MEMBERS:
            return False, f"{ens} takes at most {MAX_MEMBERS} members"
        if len(set(mem)) != len(mem):
            return False, "duplicate members"
        bad = [x for x in mem if x not in MODELS]
        if bad:
            return False, f"unknown member {bad[0]!r}"
        if ens == "soft_vote" and m not in mem:
            return False, "soft_vote: `model` must be one of the members (the lead)"
    elif mem:
        return False, f"members only apply to soft_vote/stacking (got {ens!r})"
    hyper = r.get("hyper") or {}
    levels = hyper_levels(m)
    for k, v in hyper.items():
        if k not in levels:
            return False, f"{m} has no catalog knob {k!r}"
        if v not in levels[k]:
            return False, f"{m}.{k}={v!r} is not a catalog level {levels[k]}"
    # research rules
    fams = families_of(r)
    for fam in sealed_families:
        if fam in fams:
            return False, f"family `{fam}` is sealed — recombination forbidden"
    toks = components(r)
    for combo in sealed_combos:
        combo, fam = (combo if isinstance(combo, tuple) else (combo, None))
        combo = frozenset(combo)
        if combo == toks:
            return False, "this exact combination is sealed for superadditive harm"
        if combo < toks and _extends_harm(toks - combo, fam):
            return False, "stacks another partner onto a combination sealed for superadditive harm"
    return True, "ok"


def _token_family(tok):
    if tok.startswith("model:"):
        return "partner"
    if tok.startswith("meta:"):
        return "partner"
    if tok.startswith("hyper:"):
        return FACTORS[tok[6:]].family
    return FACTORS[tok].family if tok in FACTORS else None


def _extends_harm(extra, family):
    """A superset of a harmful combination is still forbidden when everything it
    adds is more of the same kind (another partner on a harmful vote, another
    transform of the same family) — that is 'stacking another failing partner'.
    Adding a knob of a different mechanism (a regularizer on a harmful transform
    pair) is a new interaction and stays legal."""
    if family is None:
        return True
    kinds = {_token_family(t) for t in extra}
    if family in ("voting", "stacking", "bagging"):
        return kinds <= {"partner"}
    return kinds <= {family}


def is_legal(recipe, sealed_families=(), sealed_combos=()):
    return check_legal(recipe, sealed_families, sealed_combos)[0]


# --------------------------------------------------------------------- cost --
def cost_estimate(recipe):
    """Estimated wall ms for one 5-fold evaluation on the bundled dataset."""
    r = recipe
    parts = transform_parts(r)
    t_cost = sum(FACTORS[p].cost for p in parts)
    mult = 3.0 if "polynomial" in parts else 1.0
    if any(p in ("pca", "kbest") for p in parts):
        mult *= 0.7

    def model_cost(name, hyper=None):
        c = FACTORS[name].cost
        for k, v in (hyper or {}).items():
            f = FACTORS.get(hyper_factor_name(name, k, v))
            if f:
                c = max(c, f.cost)
        return c

    ens, m, mem = r.get("ensemble"), r["model"], r.get("members") or []
    if ens == "soft_vote":
        core = sum(model_cost(x, r.get("hyper") if x == m else None) for x in mem) + FACTORS[ens].cost
    elif ens == "stacking":
        core = 4 * sum(model_cost(x) for x in mem) + model_cost(m, r.get("hyper")) + FACTORS[ens].cost
    elif ens == "bagging":
        core = 6 * model_cost(m, r.get("hyper")) + FACTORS[ens].cost
    else:
        core = model_cost(m, r.get("hyper"))
    return int(t_cost + core * mult)


# --------------------------------------------------------------------- grid --
def enumerate_grid():
    """The exhaustive grid a naive search would run: every transform × every
    single model at every catalog hyper level, plus every 2–3-member vote and
    stack (logistic meta) and every bagged model — no chains, no hyper on
    ensembles. This is the denominator for 'evals used vs grid'."""
    for t in TRANSFORMS:
        for m in MODELS:
            yield make_recipe(t, m)
            for f in hyper_factors_for(m):
                yield make_recipe(t, m, hyper={FACTORS[f].param: FACTORS[f].value})
        for k in (2, 3):
            for mem in combinations(MODELS, k):
                yield make_recipe(t, mem[0], "soft_vote", members=list(mem))
                yield make_recipe(t, "logistic", "stacking", members=list(mem))
        for m in MODELS:
            yield make_recipe(t, m, "bagging")


def grid_size():
    return sum(1 for _ in enumerate_grid())


def grid_region(family):
    """How many grid cells a seal of `family` removes from the search."""
    fam = FAMILIES[family]
    return sum(1 for r in enumerate_grid() if factors_of(r) & set(fam.factors))


def catalog_summary():
    by_group = {g: [f.name for f in _F if f.group == g] for g in GROUPS}
    return {
        "factors": len(_F),
        "groups": {g: len(v) for g, v in by_group.items()},
        "by_group": by_group,
        "families": [{"name": f.name, "group": f.group, "factors": list(f.factors),
                      "mechanism": f.mechanism} for f in FAMILIES.values()],
        "grid": grid_size(),
    }


__all__ = ["Factor", "Family", "FACTORS", "FAMILIES", "GROUPS", "TRANSFORMS", "MODELS",
           "ENSEMBLES", "HYPERS", "SCALERS", "make_recipe", "recipe_id", "describe",
           "components", "factors_of", "families_of", "knobs_changed", "treatment_order", "order_name",
           "is_composite", "composite_order", "is_sub_recipe", "check_legal", "is_legal",
           "cost_estimate", "enumerate_grid", "grid_size", "grid_region", "catalog_summary",
           "family_of", "factors_in_family", "hyper_factors_for", "hyper_factor_name",
           "hyper_levels", "transform_parts"]

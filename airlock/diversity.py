"""Pick the K most *different* survivors, not the K highest-scoring.

Choice overload is worst when the options are many and similar. So after the
machine has pruned by measured fitness, we do not hand the human the top-K by
score — those tend to be near-twins. We select a diverse set: keep the single
best, then greedily add whichever remaining survivor is most unlike everything
already chosen (farthest-point / max-min sampling). Few, and clearly distinct,
is what makes the final taste call easy instead of exhausting.
"""


def _feature(v):
    """A small structural fingerprint of a variant, from its measured signals
    plus the generator's own descriptors when present."""
    sig = v.get("signals", {})
    meta = v.get("meta", {})
    return {
        "layout": meta.get("layout", "?"),
        "palette": meta.get("palette", "?"),
        "font": meta.get("font", "?"),
        "tone": meta.get("tone", "?"),
        "nodes_bucket": min(6, sig.get("nodes", 0) // 25),
        "weight_bucket": min(6, int(sig.get("weight_kb", 0) // 25)),
        "responsive": bool(sig.get("responsive")),
    }


def _distance(a, b):
    """Hamming-ish distance over categorical + bucketed features (0..n)."""
    d = 0
    for k in a:
        if k in ("nodes_bucket", "weight_bucket"):
            d += min(2, abs(a[k] - b[k])) / 2.0
        else:
            d += 1.0 if a[k] != b[k] else 0.0
    return d


def select_diverse(survivors, k):
    """survivors: list of variant dicts (each with 'fitness' and 'signals').
    Returns up to k of them, ordered [best, then most-diverse additions]."""
    if len(survivors) <= k:
        return sorted(survivors, key=lambda v: -v["fitness"])

    feats = {v["id"]: _feature(v) for v in survivors}
    by_id = {v["id"]: v for v in survivors}

    chosen = [max(survivors, key=lambda v: v["fitness"])["id"]]
    while len(chosen) < k:
        best_id, best_gap = None, -1.0
        for vid in by_id:
            if vid in chosen:
                continue
            # distance to the nearest already-chosen variant (max-min)
            gap = min(_distance(feats[vid], feats[c]) for c in chosen)
            # break ties toward higher fitness so a diverse set is still strong
            score = gap + by_id[vid]["fitness"] / 1000.0
            if score > best_gap:
                best_gap, best_id = score, vid
        chosen.append(best_id)

    return [by_id[c] for c in chosen]

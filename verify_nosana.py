#!/usr/bin/env python3
"""Verify the Nosana (OpenAI-compatible) LLM endpoint Airlock will use for narration
and ledger Q&A. Reads AIRLOCK_LLM_BASE / AIRLOCK_LLM_KEY / AIRLOCK_LLM_MODEL from env
or airlock/.env (never printed).

    echo 'AIRLOCK_LLM_BASE=https://<deployment>.node.k8s.prd.nos.ci/v1' >> .env
    python3 verify_nosana.py
"""
import sys
import time

from airlock.config import Config  # noqa: F401  (loads .env)
from airlock.llm import LLM


def main():
    llm = LLM()
    print("\n  Airlock · LLM endpoint verification\n  " + "─" * 40)
    if not llm.available():
        print("  ✗ AIRLOCK_LLM_BASE not set. Add your Nosana deployment URL to .env and rerun.")
        return 2
    print(f"  provider : {llm.provider}")
    print(f"  base     : {llm.base}")
    try:
        ms = llm.models()
        print(f"  models   : {ms[:5]}{' …' if len(ms) > 5 else ''}")
    except Exception as exc:
        print(f"  (models list unavailable: {exc}) — will use AIRLOCK_LLM_MODEL or 'default'")
    t0 = time.time()
    try:
        out = llm.narrate({"type": "champion", "spec": "vote(logistic, knn, rf)", "metric": 0.972,
                           "delta": 0.014, "evals": 23})
        print(f"  narrate  : {out!r}  ({round((time.time()-t0)*1000)} ms)")
        ans = llm.explain("Why was polynomial(2)+logistic discarded?",
                          [{"spec": "poly2+logistic", "delta": -0.009, "gate": "discard",
                            "reason": "below promote bar; contrast fine"}],
                          champion="vote(logistic, knn, rf) 0.972")
        print(f"  explain  : {ans[:160]!r}")
        print("\n  RESULT: PASS — endpoint answers; Airlock will narrate + explain via", llm.provider, "\n")
        return 0
    except Exception as exc:
        print(f"  ✗ request failed: {exc}\n  RESULT: FAIL\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())

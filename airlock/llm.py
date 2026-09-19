"""Optional LLM layer — Nosana-hosted open models (or any OpenAI-compatible endpoint).

Airlock's engine never asks a model for a *score* — fitness is measured. The model
is used for two things a measurement can't do:

  * narrate  — turn a structured research event into one calm chat line
               (falls back to deterministic templates when no endpoint is set)
  * explain  — answer a steering question ("why did poly+logistic fail?") from
               the lab's own ledger, so the answer cites measured facts

Configure (all optional; nothing breaks without them):

    AIRLOCK_LLM_BASE   e.g. https://<deployment>.node.k8s.prd.nos.ci/v1   (Nosana)
                       or https://openrouter.ai/api/v1, http://localhost:8000/v1 (vLLM)
    AIRLOCK_LLM_KEY    bearer token (Nosana deployments may not need one — leave empty)
    AIRLOCK_LLM_MODEL  model name served by the endpoint (default: the endpoint's first model)
    AIRLOCK_LLM_PROVIDER  label shown in the UI: "Nosana" | "OpenRouter" | "local" (auto-detected)

Nosana: deploy an open model (e.g. Qwen2.5 / Llama 3.x) from the Nosana dashboard;
the deployment exposes an OpenAI-compatible /v1 URL — paste it as AIRLOCK_LLM_BASE.
Verify with `python3 verify_nosana.py`.
"""
import json
import os
import urllib.request

TEMPLATES = {
    "lab_started": "Starting on {task} · metric: {metric} · {catalog}.",
    "hypothesis": "Hypothesis: {text}",
    "family_sealed": "Sealed family `{family}` — isolated, pair and triple all below the bar. Won't recombine.",
    "stagnation": "Stalled. Leading explanation: {grade}. Next: {next}",
    "champion": "Champion → {spec} · {metric} · +{delta} over best single · {evals} evals.",
}


def _provider_label(base):
    b = (base or "").lower()
    if "nos.ci" in b or "nosana" in b:
        return "Nosana"
    if "openrouter" in b:
        return "OpenRouter"
    if "localhost" in b or "127.0.0.1" in b:
        return "local"
    return "LLM" if b else "none"


class LLM:
    def __init__(self, base=None, key=None, model=None, timeout=60):
        self.base = (base if base is not None else os.environ.get("AIRLOCK_LLM_BASE", "")).rstrip("/")
        self.key = key if key is not None else os.environ.get("AIRLOCK_LLM_KEY", "")
        self.model = model if model is not None else os.environ.get("AIRLOCK_LLM_MODEL", "")
        self.provider = os.environ.get("AIRLOCK_LLM_PROVIDER") or _provider_label(self.base)
        self.timeout = timeout

    # ---- availability ----
    def available(self):
        return bool(self.base)

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.key:
            h["Authorization"] = f"Bearer {self.key}"
        return h

    def models(self):
        """List models the endpoint serves (used to pick a default model)."""
        req = urllib.request.Request(self.base + "/models", headers=self._headers())
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        return [m.get("id") for m in data.get("data", []) if m.get("id")]

    def chat(self, messages, temperature=0.2, max_tokens=300):
        """OpenAI-compatible chat completion. Raises on transport errors."""
        if not self.model:
            try:
                ms = self.models()
                self.model = ms[0] if ms else "default"
            except Exception:
                self.model = "default"
        body = json.dumps({"model": self.model, "messages": messages,
                           "temperature": temperature, "max_tokens": max_tokens}).encode()
        req = urllib.request.Request(self.base + "/chat/completions", data=body,
                                     method="POST", headers=self._headers())
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        return data["choices"][0]["message"]["content"].strip()

    # ---- product uses ----
    def narrate(self, event):
        """One calm line for the chat. Deterministic template unless an LLM is set."""
        kind = event.get("type", "")
        fallback = TEMPLATES.get(kind, "").format_map(_Safe(event)) if kind in TEMPLATES else ""
        if not self.available():
            return fallback
        try:
            sys_p = ("You narrate an auto-research agent's progress in one short, concrete sentence "
                     "(≤ 25 words). Never invent numbers; only restate the fields given. "
                     "Tone: calm, precise, no filler, no emojis.")
            return self.chat([{"role": "system", "content": sys_p},
                              {"role": "user", "content": json.dumps(event, ensure_ascii=False)}],
                             temperature=0.1, max_tokens=80) or fallback
        except Exception:
            return fallback

    def explain(self, question, ledger_rows, champion=None):
        """Answer a steering question from measured facts only (RAG over the ledger)."""
        facts = "\n".join(json.dumps(r, ensure_ascii=False) for r in ledger_rows[-40:])
        if not self.available():
            return ("(no LLM endpoint configured — set AIRLOCK_LLM_BASE, e.g. a Nosana deployment)\n"
                    f"Ledger has {len(ledger_rows)} rows; champion: {champion}")
        sys_p = ("You answer questions about an ML structure search using ONLY the ledger rows given. "
                 "Cite specs and deltas verbatim. If the ledger doesn't contain the answer, say so. "
                 "Be brief (≤ 80 words).")
        try:
            return self.chat([{"role": "system", "content": sys_p},
                              {"role": "user", "content": f"LEDGER:\n{facts}\n\nCHAMPION: {champion}\n\nQUESTION: {question}"}],
                             temperature=0.1, max_tokens=220)
        except Exception as exc:
            return f"(LLM error: {exc})"


class _Safe(dict):
    def __missing__(self, k):
        return "?"


def get_llm():
    return LLM()

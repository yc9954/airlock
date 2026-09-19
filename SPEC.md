# Airlock — composite-structure auto-research agent (contract)

This file is the contract every implementing agent builds against. Read it fully before touching code.

## 0. Thesis (what we are proving)

"More agents" and "brute-force grid search" are both wrong. A single method is rarely optimal; a
**composite** (recipe) of methods often beats any single method — but composites interact
non-additively (they can help *or* hurt), so naive recombination explodes combinatorially. The
value is **selection pressure with discipline**: test factors and atomic recipes against a real
metric, seal families that are proven dead, and only ever run the *cheapest experiment that can
falsify the current leading explanation*. This is the methodology of a real auto-research campaign
(hundreds of experiments, a handful of promotions); Airlock productizes it and runs the experiments as
parallel Daytona sandboxes.

Tagline: **Selection pressure, not agent spam.**

### Methodology to encode
- **Objective**: one primary metric decides everything (here: held-out CV score). Proxies never
  promote. Ties prefer simplicity (fewer components).
- **Lanes**: `explore` (cheap screens → candidates only), `confirm` (declared axis — ONE factor or
  ONE atomic recipe — control vs treatment, same budget → keep/discard/repeat), `repair`
  (harness fixes, never counted as science).
- **Gates**: `promote | keep | discard | explore_only | repeat | crash`. A crash is NOT a scientific
  discard.
- **Promote bar**: treatment must beat control by ≥ `min_delta` (task-specific; e.g. 0.003 accuracy)
  with a falsifier declared up front.
- **Family characterization**: a *family* = a set of knobs of one kind. If isolated, pair, and
  triple combinations from the same family all fail the bar, mark the family `characterized` and
  **forbid recombination**. A recipe miss does NOT close its isolated factors.
- **Why-critique on stagnation**: if best doesn't improve ≥ min_delta for K rounds, pick a
  stagnation grade — `local_optimum | family_tax | superadditive_harm | measurement_resolution |
  frozen_axis | information_gain` — write two competing explanations and the observation that
  separates them, then choose the next experiment to *falsify* the leading one.
- **Selection rule**: run the **cheapest unresolved experiment that can falsify the current leading
  explanation** and that passes the information-gain gate (has a new-information goal; not a
  characterized-family recombination; not a closed-bracket retry; not a "comfort recipe"). If nothing
  qualifies → HOLD (no chip burn), then a literature/analysis cycle.
- **Ledger**: every experiment appended as JSON (spec, control, treatment, metric, delta, gate,
  reason). Champion tracked separately and only replaced by a `promote`.

## 1. Demo task (fixed for the hackathon)

**Tabular ML composite search** — fast, real, measurable, and composite-beats-single is demonstrable.
- Dataset: a bundled, deterministic tabular classification set with real nonlinear structure and
  some noise (loaded via `sklearn.datasets` — prefer `load_breast_cancer` + engineered noise, or
  `make_classification` with fixed seed; the design phase decides and justifies).
- Metric: stratified k-fold CV accuracy (or AUC), fixed folds/seed, ± std. `min_delta` chosen so
  meaningful improvements pass and noise doesn't.
- **Candidate catalog** — factors grouped into families, each factor a single component:
  - `transform` family: none, standardize, polynomial(2), PCA(k), select-k-best, quantile
  - `model` family: logistic, knn, decision tree, random forest (small), gradient boosting (small),
    naive bayes, linear SVM, MLP (small)
  - `ensemble` family: none, soft-voting(2–3 models), stacking(base models → meta), bagging
  - `regularization`/`hyper` family: a few discrete levels per model
  - (design phase may add/remove; keep ≤ ~40 factors so the catalog is legible in the UI)
- **Recipes** = compositions (transform × model × ensemble × hyper). An *atomic recipe* is a named,
  declared combination tested as one axis.
- Success criteria for the demo (must be verified by tests):
  1. The discovered champion is a **composite** (ensemble/stack or transform+model) that beats the
     best single model by ≥ min_delta.
  2. The disciplined search reaches the champion using **fewer evaluations** than exhaustive grid
     over the same catalog, and it **seals ≥1 family** along the way.
  3. At least one composite is shown to *hurt* (superadditive harm) — the chat surfaces it.

## 2. Engine (Python, `airlock/research/`)

Reuse Airlock's existing pieces (`airlock/sandbox.py` Daytona adapter, `server.py` SSE broker,
`config.py` dotenv). New package `airlock/research/`:
- `catalog.py` — factors, families, recipe composition, cost estimate per candidate, legality rules.
- `evaluate.py` — builds an sklearn pipeline from a recipe spec and returns the metric (+std, cost,
  params count). Deterministic given seed.
- `runners.py` — `LocalRunner` (multiprocessing pool, `n = cpus-2`) and `DaytonaRunner`:
  a **pool of N Daytona sandboxes provisioned once** (image with scikit-learn; boot ~5s each),
  each pulling candidates from a queue via `process.exec` — so per-candidate cost is seconds, not a
  boot. Same interface: `evaluate_many(specs) -> results` with per-result runtime info (sandbox id,
  ms). Falls back to Local when no `DAYTONA_API_KEY`.
- `loop.py` — the research loop above: explore screens → hypotheses → confirm (control vs
  treatment) → gates → ledger → family characterization → why-critique → cheapest-falsifier
  selection → champion. Emits **events** (below). Supports *steering* messages from the user
  (`focus family`, `stop`, `why did X fail`, `run N more`) and *ask* events that pause for approval
  when a step is expensive.
- `ledger.py` — JSONL append + champion state, per lab.

### Event schema (SSE `data:` JSON, one per line) — the chat and the right panel render these
- `lab_started {lab_id, task, metric, catalog_summary, providers, budget}`
- `hypothesis {id, text, kind: factor|recipe, family, control, treatment, cost_est, why}`
- `experiment_started {id, hypothesis_id, lane, spec, runner: local|daytona, sandbox_id?}`
- `experiment_result {id, metric, std, delta_vs_control, cost_ms, gate: promote|keep|discard|explore_only|repeat|crash, reason}`
- `family_sealed {family, evidence: [{spec, delta}], text}`
- `stagnation {grade, explanations: [a, b], separating_observation, next}`
- `champion {spec, metric, delta_vs_best_single, evals_used, evals_grid_equivalent}`
- `ask {id, question, options: [{id,label,detail}], reason}` → answered via `POST /api/labs/{id}/answer`
- `agent_message {text, cards?: [...] }` — calm narration for the chat (see §4)
- `progress {evals_done, evals_grid, best_metric, best_single_metric, history: [{round, best, mean, worst}]}`
- `done {summary}`

### HTTP API (extend `airlock/server.py`, stdlib only)
- `POST /api/labs` `{prompt, task?, budget?}` → `{lab_id}` (starts the loop in a worker thread)
- `GET  /api/labs/{id}/stream` → SSE of the events above (replay from 0 on connect)
- `POST /api/labs/{id}/steer` `{text}` → the agent treats it as a steering message (returns ack)
- `POST /api/labs/{id}/answer` `{ask_id, option_id}`
- `GET  /api/labs` → list `{id, title, last_message, updated, status}` (sidebar)
- `GET  /api/health` → providers + config
- serve the built frontend from `web/dist/` at `/` when present

## 3. Frontend (`web/`, Vite + React 19 + Tailwind v4) — rakazo's design, exactly

Reference sources are saved at
`/private/tmp/claude-501/-Users-yuchanlee-devpost-scout/4e0f8231-6646-4368-8b2b-ccc63016bdc9/scratchpad/rakazo-src/`
(tokens.css, Shell.tsx layout, message-cards.tsx, bot-panel.tsx, bot-picker.tsx, primitives.tsx,
bot-avatar.tsx, button/input/textarea/badge). Hero screenshot: `../rakazo-hero.png`.
- Use `tokens.css` **verbatim** (Apache-2.0 — add `NOTICE` crediting rakazo for design tokens),
  `@theme inline` mapping as in `ui-web/styles.css`, Geist Variable font, `data-theme="dark"` default.
- **Layout = rakazo Shell**: left sidebar (~316px, `bg-sidebar`, search box, list of **labs** with
  colored bot-style avatars, name, last-message preview, timestamp, active dot); center chat
  (header with lab avatar + title + status pill; messages: agent bubbles `rounded-[20px] bg-muted
  max-w-[74%]`, user bubbles `bg-chat-user` right-aligned, centered timestamps; result cards
  `rounded-[20px] border border-border bg-card` with ✓/✗ checklist rows like rakazo's Inbox card;
  ask cards with option buttons like `AskCard`; composer at bottom `rounded-xl bg-card border
  border-border focus-within:border-ring`); right panel (`md:w-[384px]`, "Airlock's computer"):
  **fitness chart** (best/mean/worst band climbing per round), **running sandboxes** (Daytona ids,
  ms, live dots), **catalog & families** (chips; sealed families crossed out), **champion** card,
  and a "Research routines" list like rakazo's Routines.
- Calm surface, rigorous underneath (rakazo VISION): the chat shows hypotheses, launched
  experiments (compact), results (✓ kept / ✗ discarded / ⊘ family sealed), stagnation critiques,
  asks, and the champion — never raw tool lifecycle. Details live in the right panel.
- Fonts/sizes/radii/colors must match rakazo's classes (see saved sources). Mobile: sidebar
  collapses like rakazo.
- Dev: `npm run dev` proxies `/api` to the Python server (port 8770). `npm run build` → `web/dist`.

## 4. Chat narration (the agent's voice)
Short, concrete, no filler. Examples:
- "Starting on `breast_cancer` · metric: 5-fold accuracy · catalog: 31 factors in 4 families."
- card ✓ "Kept → standardize + logistic  0.951 (+0.004 vs control)"
- card ✗ "Discarded → polynomial(2) + logistic  0.938 (−0.009)"
- card ⊘ "Sealed family `polynomial` — isolated, pair, triple all below the bar. Won't recombine."
- "Stalled 3 rounds. Leading explanation: local optimum on single models. Competing: ensembles
  untested. Cheapest falsifier: soft-vote(logistic, knn) — 2 evals."
- card ★ "Champion → stack(logistic, rf → logistic)  0.972 · +0.014 over best single · 23 evals
  (grid would need 180)"
- ask: "Run the 6 remaining stacking recipes in Daytona? ~40s, 6 sandboxes." [Run] [Skip]

## 5. Repo layout after this work
```
airlock/
  airlock/            python package (existing + research/)
  web/                Vite React app (rakazo-style)  ← replaces the old vanilla console
  tests/              engine + API tests (offline, deterministic)
  demo/               assets
  SPEC.md             this contract
  NOTICE              rakazo design-token attribution (Apache-2.0)
```
Keep everything runnable offline with `./run.sh` (Python server + built frontend). Daytona is an
upgrade path via `.env` (already verified working in `verify_daytona.py`).

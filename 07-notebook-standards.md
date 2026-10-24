# 07: Notebook Standards

Each lab has exactly one primary notebook: `labs/labNN-<slug>/notebooks/labNN_<slug>.ipynb`. The notebook **is** the lab. A learner who never opens a `.py` file should still understand everything.

## 1. Mandatory structure

| # | Section (Markdown H2) | Contents |
|---|---|---|
| 0 | Title cell (H1) | Lab number and name; a one-sentence goal; estimated time; prerequisites (previous labs); **"What you'll learn"** (3–6 bullets); A2A spec sections touched (links). |
| 1 | **Architecture** | A Mermaid diagram of services and calls, plus a table of services, framework, port and role. Spells out the pattern in words: "Who owns the task? Who sees what?" |
| 2 | **Setup** | One code cell: imports, `init_telemetry("notebook-labNN", framework="notebook")`, `show_models()`, endpoint table, and a wait for services (`await wait_for_cards([...])`). On failure it prints the exact command to fix things, e.g. `make up LAB=lab05`. |
| 3 | **Concept primer** | Short prose (≤250 words) on the pattern and the protocol pieces involved, with a sequence diagram (Mermaid `sequenceDiagram`) of the wire exchange. |
| 4..n | **Steps** | Each step has an H3 title and the pattern **Explain → Code → Observe**: (a) Markdown saying what will happen and why; (b) one focused code cell, normally ≤25 lines; (c) output shown through `display.*` helpers; (d) a short Markdown "What just happened" (2–4 bullets) naming the A2A constructs seen: `Task`, `TaskState`, `Artifact`, `contextId`, and so on. |
| n+1 | **See it in Grafana** | `trace_link()` for the main trace, a screenshot placeholder description, the TraceQL to try and the LogQL to try. |
| n+2 | **Exercises** | 2–4 exercises, graded easy/medium/stretch, each with a hidden solution cell (a collapsed cell tagged `solution`, jupytext metadata `"jupyter": {"source_hidden": true}`). |
| n+3 | **Recap and next** | A key-takeaways table (pattern, when to use, when not to), common pitfalls and a link to the next lab. |
| n+4 | **Cleanup** | `await client.aclose()`, `tel.flush()`, and an optional `make down` note. |

## 2. Code rules

- **Top-level `await`** in cells. IPython supports it, so no `asyncio.run`. Do not use `nest_asyncio` except where calling a sync wrapper that runs its own loop, such as Strands `A2AAgent.__call__`. In that case prefer `await agent.invoke_async(...)`.
- Cells must be **idempotent and rerunnable**. Use fresh `context_id`s via `new_context_id()` where a clean slate matters.
- No hidden state: variables used later are defined in visible cells.
- Show the **raw A2A objects** at least once per lab, using `MessageToDict` and the `display.show_wire` view, before switching to helpers.
- Each LLM-dependent cell prints `⏱` timing and the model role used. The first LLM call warns "first call may take 30–90 s while Ollama loads the model".
- Each cell ≤25 lines. Longer logic goes into the lab package and is imported, with a link to the source path.
- Every code cell starts with a one-line comment describing intent: `# Send a streaming message and watch task states arrive`.
- Errors are teaching moments. Cells that demonstrate an error wrap it in `try/except` and show the A2A error code and reason.

## 3. Writing style

- Second person, present tense, plain language. Define every acronym on first use per notebook.
- Prefer tables to long prose for comparisons.
- Use these callouts as blockquotes with emoji labels: `> 💡 Tip`, `> ⚠️ Pitfall`, `> 🔍 Under the hood`, `> 🧪 Try it`.
- Reference spec sections by link: `[spec §3.1.2](https://a2a-protocol.org/latest/specification/#...)`.
- Use the protocol terms consistently: "Agent Card", "Task", "Message", "Part", "Artifact", "context". Use **v1.0 method names**, and give legacy and v0.3 names in parentheses the first time each appears in a notebook. For example: `SendStreamingMessage` (v0.3 `message/stream`; legacy `tasks/sendSubscribe`).

## 4. Execution and CI

- Kernel: `python3` from the workspace venv (Docker `jupyter` service or `uv run jupyter lab`).
- Notebooks are stored **with outputs cleared** (enforced by `nbstripout` pre-commit). A rendered copy with outputs is generated in CI and attached as an artifact (`*.executed.ipynb`).
- Cells tagged `skip-ci` are skipped by nbmake: exercises, long LLM loops, and cells that intentionally hang until interrupted.
- Every notebook must execute end to end with `papermill` in under **10 minutes** on the reference machine with the default models. Lab 00 must finish in under 2 minutes because it uses no LLM.
- A `parameters` cell (papermill) exposes `QUICK_DEMO = False`. When True, it reduces iteration counts and swaps the reasoning role for quick.

## 5. Root notebook `notebooks/00-setup-and-observability.ipynb`

1. Checks the environment: Python version, workspace packages importable, `.env` loaded.
2. Checks models: calls the logic of `scripts/check_models.py` and renders the table. It also demonstrates one raw `/v1/systemone` call to Nimble, explaining what a decision model is and why it is not a chat model.
3. Checks observability: emits a test span, log and metric, then queries the Tempo, Loki and Prometheus APIs on lgtm to prove arrival.
4. Tours Grafana: links to the dashboards and Explore, with sample TraceQL, LogQL and PromQL.
5. Shows how to switch providers: the env vars table, and how to re-run `show_models()` after editing `.env`.

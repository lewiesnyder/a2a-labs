# AGENTS.md: Instructions for Coding Agents Building `a2a-labs`

You are building a teaching monorepo from this specification. Read this file fully before writing code.

## 1. Order of reading

1. `00-overview.md`: what and why, plus the key decisions (D1–D12). They are binding.
2. `01-protocol-reference.md`: the protocol you implement against.
3. `10-pitfalls-and-corrections.md`: traps that will otherwise cost you hours.
4. `02`–`08`: cross-cutting specs.
5. `09-build-plan.md`: what to build, in which order, and what "done" means.
6. `labs/labNN-*.md`: per-lab specs, read when you reach that milestone.

## 2. Rules of engagement

1. **Use official constructs.**
   - Before writing any A2A plumbing, check whether `a2a-sdk`, ADK, Strands or LangGraph already provides it, and use it if so.
   - Custom code is allowed only where the spec says `# CUSTOM` or where a [VERIFY] check proves no official construct exists. Mark such code with `# CUSTOM: <reason>` and list it in `docs/custom-code.md`.
2. **Target A2A v1.0 / `a2a-sdk` 1.2.x APIs.**
   - Never use 0.3-era APIs: `A2AStarletteApplication`, `A2AFastAPIApplication`, `TextPart`/`FilePart`/`DataPart` classes, `ClientFactory()`, `A2AClient`, lowercase `TaskState.working`.
   - If you find a code sample using them, translate it.
3. **Never hard-code models, hosts or ports.** Use `a2a_labs_common.models.*` and `a2a_labs_common.config.endpoint()/port()`.
4. **Telemetry first.** `init_telemetry()` is the first call in every `main()` and in every notebook setup cell.
5. **Deterministic tests.** Every lab passes its integration tests with `LLM_PROVIDER=fake` and `DECISION_PROVIDER=fake`.
6. **Small modules, clear comments.** Use `# A2A:` comments to explain protocol significance. This is a teaching repo, so readability beats cleverness.
7. **Keep spec and code in sync.** If reality differs from the spec, change the code to match reality, and record the change in `docs/spec-deviations.md` with the spec section, what you found, what you did and evidence (a link, a `help()` output or a version). This applies to package APIs, AGP spec details, Ollama behavior and so on. Do not silently diverge.

## 3. Verification gates ([VERIFY] markers)

The spec marks uncertain API details with **[VERIFY]**. For each one, before coding against it:

1. Install the pinned versions (`uv sync`).
2. Introspect:
   - `uv run python -c "import a2a.helpers as h; print([n for n in dir(h) if not n.startswith('_')])"`
   - `uv run python -c "from a2a.server.tasks import TaskUpdater; help(TaskUpdater)"`
   - `uv run python -c "from google.adk.a2a.utils.agent_to_a2a import to_a2a; import inspect; print(inspect.signature(to_a2a))"`
   - `uv run python -c "from strands.multiagent.a2a import A2AServer; import inspect; print(inspect.signature(A2AServer.__init__))"`
3. Read the official docs or source (URLs in `appendices/E-sources.md`).
4. Record the result in `docs/verification-log.md` as a table of item, spec location, finding, decision and date.

Milestone M0 (see 09) consists of resolving all [VERIFY] items for `libs/a2a-labs-common` and Lab 00.

## 4. Environment facts

- Default LLMs run in the **host's** Ollama (`OLLAMA_MODE=host`), reached from containers at `http://host.docker.internal:11434`.
- Models: `qwen3.8:27b` (reasoning), `llama3.2:latest` (quick), `nimble-v3:latest` (decision, via `POST /v1/systemone`, **not chat**; needs Ollama ≥ 0.40.4; CC BY-NC 4.0).
- Observability: `grafana/otel-lgtm` at :3000 (UI) and :4317/:4318 (OTLP).
- Python 3.12, uv workspace, one Docker image `a2a-labs:dev`.

## 5. Definition of done (repo level)

- `make setup && make test` is green.
- For each lab: `make e2e LAB=labNN` is green in fake mode, and the notebook runs green with real models.
- TCK mandatory tests pass for Lab 00.
- The trace connectivity test passes.
- `appendices/A-coverage-matrix.md` is fully covered. The CI script `scripts/check_coverage_tags.py` passes.
- `docs/verification-log.md`, `docs/spec-deviations.md` and `docs/custom-code.md` exist and are current.
- The README quickstart works on a clean machine with Docker and Ollama installed.

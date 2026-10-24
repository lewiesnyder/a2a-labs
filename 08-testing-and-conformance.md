# 08: Testing and Conformance

## 1. Test pyramid

| Layer | Location | Needs | Runs in | Contents |
|---|---|---|---|---|
| Unit | `libs/*/tests`, `labs/*/tests/test_cards.py`, `test_executors.py` | nothing | `make test`, CI on every push | Card builders and validation; model config resolution; decision response parsing; method crosswalk constant; executors driven in-process via `testing.asgi_client` with fake models; push JWT sign/verify; registry signature verify; AGP route table logic. |
| Integration | `labs/*/tests/test_integration.py` (`@pytest.mark.compose`) | the lab's compose profile | `make e2e LAB=…` | Real HTTP to running services. With `SKIP_LLM=1`, LLM labs swap in fake models through `LLM_PROVIDER=fake` [see §4]. |
| Notebook | each lab notebook | compose profile + models | `make e2e`, nightly CI | `pytest --nbmake` or papermill. Must complete without errors. |
| Conformance | `scripts/run_tck.sh` | running SUT | nightly CI, `make tck` | a2a-tck against Lab 00 and at least one server per framework. |
| Trace connectivity | `tests/e2e/test_trace_connectivity.py` | compose + lgtm | nightly | Asserts the cross-service trace (05 §8). |

## 2. Required unit tests per lab (minimum)

- `test_cards.py`: every server's card builds, passes `validate_card`, declares the right capabilities (streaming, push and extensions per the lab spec) and lists only the bindings the server actually serves.
- `test_executors.py` (pure a2a-sdk and LangGraph executors):
  - The first event is a `Task`, or the only event is a `Message`.
  - Each state path in the lab spec is reachable.
  - Artifacts are emitted before COMPLETED.
  - `cancel()` leads to CANCELED.
  - INPUT_REQUIRED → resume with the same `task_id` → COMPLETED.
- Lab-specific tests listed in each lab spec under "Acceptance tests".

## 3. A2A TCK and Inspector

- **TCK:** `a2aproject/a2a-tck`. `scripts/run_tck.sh <service-url> [--category mandatory|capabilities|all]` clones a pinned TCK commit into `.cache/`, installs it with `uv`, then runs `./run_tck.py --sut-host <url>` [VERIFY current CLI flags].
  - CI gates on **mandatory** tests for `lab00-protocol-agent`.
  - CI reports, but does not gate, capability tests for `lab01-adk-agent`, `lab01-strands-agent` and `lab01-langgraph-agent`.
  - Known framework deviations go in `docs/conformance.md`, generated from the TCK JSON report.
- **A2A Inspector:** profile `tools`, at http://localhost:5001. Lab 00 and Lab 01 notebooks include an optional step: "Open the Inspector, paste `http://lab01-adk-agent:10102`, and inspect the card and a live message exchange."

## 4. Fake provider for CI

`LLM_PROVIDER=fake` (any role) makes the adapters return scripted fakes from `a2a_labs_common.testing`. Scripts are keyed by role and prompt-substring in `config/fake_responses.yaml`. They include tool-call scripts, so agent-as-tool flows can run deterministically. `DECISION_PROVIDER=fake` returns the first option or `noul=0.9`, unless the script says otherwise.

LLM-backed labs MUST pass their integration tests in fake mode. Notebooks run against real models nightly; there is no fake-mode notebook run.

## 5. CI workflows

`ci.yaml` (push/PR):
- `uv sync --frozen`
- `ruff check`, `ruff format --check`
- `mypy libs/`
- `pytest -m "not compose and not llm"`
- `docker build`

`e2e.yaml` (nightly and manual):
- A matrix over `lab00`…`lab10`.
- For each lab: `docker compose --profile $LAB up -d --wait` with `LLM_PROVIDER=fake`, `SKIP_MODEL_CHECK=true`, then `pytest -m compose labs/$LAB`, then a TCK run where applicable, then `compose down`.
- A separate self-hosted GPU runner job (optional) runs the notebooks with real Ollama models.

## 6. Definition of done for a lab

1. Code, notebook, README and tests are present. `ruff` and `mypy` are clean.
2. `make e2e LAB=labNN` passes in fake mode, and the notebook runs green with real models on the reference machine.
3. Every item in the lab spec's "Acceptance tests" list is automated or explicitly marked manual, with a reason.
4. The trace connectivity check passes for the lab's main flow.
5. The coverage matrix (`appendices/A-coverage-matrix.md`) cells claimed by the lab are demonstrably exercised. Each notebook step that demonstrates a matrix cell is tagged in cell metadata (`"a2a_coverage": ["SendStreamingMessage", "pattern:2"]`), and a CI script cross-checks the tags against the matrix.

# Conformance (A2A TCK)

Run: `make tck SUT=lab00-protocol-agent` (wraps `scripts/run_tck.sh`, TCK commit
`d4d6fae7887f88ef607fb461c13e3b0e1802f19b`, MUST level, all transports on the card).

## `lab00-protocol-agent` (a2a-sdk 1.2.3), 2026-10-24

| Transport | MUST result |
|---|---|
| JSON-RPC | **all pass** (the gate in spec 08 §3) |
| HTTP+JSON | 2 fail (SDK behaviour, below) |
| gRPC | 2 fail (SDK behaviour, below) |

212 passed, 4 failed, 19 skipped (capability not declared).

### Known deviations (all come from `a2a-sdk` 1.2.3, not from lab code)

| TCK id | Transport | Expected | SDK does | Notes |
|---|---|---|---|---|
| CORE-CANCEL-002 | HTTP+JSON | HTTP 409 for `TaskNotCancelableError` | HTTP 400 `FAILED_PRECONDITION`, reason `TASK_NOT_CANCELABLE` | The SDK's REST error table maps it to 400. The reason string is correct. |
| STREAM-SUB-003 | gRPC | `UNIMPLEMENTED` for subscribing to a terminal task | `FAILED_PRECONDITION` | |
| HTTP_JSON-SVC-001 | HTTP+JSON | `Content-Type: application/json` | `application/a2a+json` | Spec 1.0.1 prefers `application/a2a+json`; this TCK commit still expects `application/json`. |
| GRPC-ERR-002 | gRPC | `UNIMPLEMENTED` (`VersionNotSupportedError`) for an unsupported `A2A-Version` | request processed normally | The SDK validates the version header on HTTP bindings only. |

### TCK quirks we adapted to (not SDK or lab defects)

| TCK id | Quirk | What we do |
|---|---|---|
| CORE-SEND-003 | Titled "returns ContentTypeNotSupportedError" but sets no `expected_error`, so it passes only if the unsupported media type is *accepted* | `full` keeps the SDK default (no input-mode validation). `nostream` and `nopush` validate strictly so Lab 00 can show `-32005` (DEV-10). |
| all `tck-*` ids | The TCK drives the agent with in-band `messageId` prefixes (see its `scenarios/*.feature`) | `TckExecutor` (`# CUSTOM`) answers them and delegates everything else. |
| `test-resubscribe-message-id*` | The task must stay alive for at least 2 x `TCK_STREAMING_TIMEOUT` | `TckExecutor` sleeps `2 x timeout + 0.5 s`. |

Why we did not patch the SDK: the labs teach what the SDK really does, and notebooks for
Labs 01+ build on it. Patching its error tables here would hide behaviour learners will meet
elsewhere. Re-run the TCK after each SDK upgrade and delete rows that are fixed.

## Lab 01 framework servers (report only, not a gate), 2026-10-24

`./scripts/run_tck.sh http://localhost:<port> --level must`, servers run on the host with
`LLM_PROVIDER=fake`; they declare JSON-RPC only, so REST and gRPC tests are skipped.
Summarise with `uv run scripts/tck_summary.py`.

| Server | Framework | JSON-RPC MUST tests passed | MUST compatibility |
|---|---|---|---|
| `lab01-adk-agent` (:10102) | ADK `to_a2a` | 64 of 85 (6 failed) | 69.1% |
| `lab01-strands-agent` (:10103) | `StrandsA2AExecutor` | 65 of 85 (5 failed) | 70.4% |
| `lab01-langgraph-agent` (:10104) | `LangGraphA2AExecutor` | 65 of 85 (5 failed) | 70.4% |

| TCK id | Servers | Why | Fixable? |
|---|---|---|---|
| DM-ART-001 | all three | Expects the artifact text `Generated text content`; an LLM agent answers with its own text | No: the test scripts the agent (Lab 00's `TckExecutor` handles it) |
| DM-MSG-001 | all three | Expects a direct `Message`; these agents always create a Task | No: a design choice of the framework adapters (`sdk-echo` is the Message agent) |
| DM-SERIAL-001 | ADK only | ADK puts snake_case keys in `metadata` (`adk_author`, `adk_session_id`, `adk_invocation_id`, ...) but A2A JSON uses camelCase | Upstream (google-adk); record and re-test after upgrades |

Everything else that applies to JSON-RPC passes for all three, including task lifecycle, streaming,
cancel and history. The framework adapters are conformant where the agent's behaviour does not
matter; the three failures are about *what the agent says*, not about the protocol plumbing.

## Repository gates (M13)

| Gate | Command | Result |
|---|---|---|
| Lint, format, types | `make lint` | clean |
| Unit and in-process integration tests (fake models) | `make test` | ~400 passed, ~95% line coverage of `a2a-labs-common` |
| Notebook coverage tags vs `appendices/A-coverage-matrix.md` | `make coverage-tags` | 151/151 cells tagged, no unknown tags |
| Per-lab `make e2e LAB=labNN` (fake mode: compose tests + notebook) | CI `e2e.yaml` matrix | green when each milestone landed (see `git log`) |
| TCK, JSON-RPC MUST | `make tck SUT=lab00-protocol-agent` | all pass (above) |

The TCK is only run against Lab 00: it drives agents through in-band `messageId` prefixes that only `TckExecutor` implements, so the Lab 01 framework servers would fail scenarios that test the TCK's protocol, not theirs. The spec's capability reports for those servers are therefore not produced (see DEV-12).


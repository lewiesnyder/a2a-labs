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

# Appendix A: Coverage Matrix

Notebook cells that demonstrate a cell of this matrix carry `metadata.a2a_coverage` tags with the exact keys in the **Tag** column. `scripts/check_coverage_tags.py` fails CI if any ✔ cell has no tagged notebook cell in the listed lab.

## A.1 Operations × labs

★ marks the seven methods requested by name, given with their legacy names.

| Operation (v1.0) | Legacy | Tag | L00 | L01 | L02 | L03 | L04 | L05 | L06 | L07 | L08 | L09 | L10 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SendMessage ★ | `tasks/send` | `op:SendMessage` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| SendStreamingMessage ★ | `tasks/sendSubscribe` | `op:SendStreamingMessage` | ✔ | ✔ | | | ✔ | ✔ | | ✔ | ✔ | | |
| GetTask ★ | `tasks/get` | `op:GetTask` | ✔ | ✔ | ✔ | ✔ | ✔ | | | ✔ | ✔ | ✔ | ✔ |
| ListTasks | — | `op:ListTasks` | ✔ | | | | | | | ✔ | | | ✔ |
| CancelTask ★ | `tasks/cancel` | `op:CancelTask` | ✔ | | | ✔ | ✔ | | | ✔ | ✔ | | |
| SubscribeToTask ★ | `tasks/resubscribe` | `op:SubscribeToTask` | ✔ | | | | ✔(error) | | | | ✔ | | |
| CreateTaskPushNotificationConfig ★ | `tasks/pushNotification/set` | `op:CreatePushConfig` | ✔ | | | | | | | | ✔ | | ✔ |
| GetTaskPushNotificationConfig ★ | `tasks/pushNotification/get` | `op:GetPushConfig` | ✔ | | | | | | | | ✔ | | |
| ListTaskPushNotificationConfigs | — | `op:ListPushConfigs` | ✔ | | | | | | | | ✔ | | |
| DeleteTaskPushNotificationConfig | — | `op:DeletePushConfig` | ✔ | | | | | | | | ✔ | | |
| GetExtendedAgentCard | — | `op:GetExtendedAgentCard` | ✔ | | | | | | ✔ | | | | ✔ |
| Card discovery (well-known) | — | `op:Discovery` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Legacy name rejected (-32601) | `tasks/send` | `legacy:rejected` | ✔ | | | | | | | | | | |
| v0.3 compat names | `message/send` etc. | `compat:v03` | ✔ | | | | ✔ | | | | | | ✔ |

## A.2 Task states × labs

| State | Tag | Where demonstrated |
|---|---|---|
| SUBMITTED | `state:SUBMITTED` | L00, L08 |
| WORKING | `state:WORKING` | L00, L01, L04, L07, L08 |
| INPUT_REQUIRED | `state:INPUT_REQUIRED` | L00, L02, L04, L05, L07 |
| AUTH_REQUIRED | `state:AUTH_REQUIRED` | L00, L10 |
| COMPLETED | `state:COMPLETED` | all |
| FAILED | `state:FAILED` | L00, L05 (degraded), L06 (rogue card) |
| CANCELED | `state:CANCELED` | L00, L04, L07, L08 |
| REJECTED | `state:REJECTED` | L00, L08 (push URL refused), L10 (Lab 09 returns AGP errors as JSON-RPC errors instead, DEV-63) |

## A.3 Patterns × frameworks

| Pattern | Tag | a2a-sdk | ADK | Strands | LangGraph/LangChain | Lab |
|---|---|---|---|---|---|---|
| 1 Direct client → agent (Message vs Task) | `pattern:1` | ✔ server + client | ✔ `to_a2a` | ✔ `A2AServer` | ✔ executor | L01 (L00) |
| 2 Agent as tool | `pattern:2` | — | ✔ `AgentTool(RemoteA2aAgent)` | ✔ `make_a2a_client`, `A2AAgent` in `@tool` | ✔ `@tool` over the a2a client | L02 |
| 2b Delegation via MCP | `pattern:2b` | ✔ (bridge client) | ✔ `McpToolset` | ✔ `MCPClient` | ✔ `langchain-mcp-adapters` | L03 |
| Sub-agent transfer (other pattern) | `pattern:subagent` | — | ✔ `sub_agents=[RemoteA2aAgent]` | — | — | L02, L05 |
| Graph node (other pattern) | `pattern:graphnode` | — | ✔ `SequentialAgent` | ✔ `GraphBuilder` + `A2AAgent` | ✔ `RemoteA2ANode` | L04, L05, L07 |
| 3 LangGraph framework node | `pattern:3` | ✔ executor | — | — | ✔ (+ Agent Server) | L04 |
| 4 Supervisor/router | `pattern:4` | — | ✔ coordinator | ✔ Graph supervisor | ✔ supervisor + Nimble | L05 |
| 5 Registry/discovery | `pattern:5` | ✔ signing, resolver | ✔ injected card | ✔ verify-then-construct | ✔ delegator | L06 |
| 6 Sequential chain / multi-hop | `pattern:6` | ✔ orchestrator, relay | ✔ planner, `SequentialAgent` | ✔ writer, Graph | ✔ editor, linear graph | L07 |
| 7 Async queue/push | `pattern:7` | ✔ | ✔ | ✔ | ✔ | L08 |
| 8 AGP squads (experimental) | `pattern:8` | ✔ gateways | ✔ squad | ✔ squad | ✔ squad | L09 |

## A.4 Cross-cutting features

| Feature | Tag | Labs |
|---|---|---|
| Transports: JSON-RPC / REST / gRPC | `transport:jsonrpc`, `transport:rest`, `transport:grpc` | L00 (all three); others JSON-RPC |
| Part kinds: text / raw / url / data | `part:text`, `part:raw`, `part:url`, `part:data` | L00; data also L04, L07, L09, L10 |
| Chunked artifacts | `artifact:chunked` | L00, L01, L07, L08 |
| Multi-turn `context_id` | `context:multiturn` | L00, L01, L05 |
| `reference_task_ids` | `refs` | L00, L07 |
| Error catalog | `errors` | L00 (all), L09 (AGP), L10 |
| Version negotiation | `versioning` | L00, L10 |
| Security schemes / auth | `security` | L10 (L06 extended card) |
| Card signing | `signing` | L06 |
| Extensions (optional/required/ADK/AGP) | `extensions` | L10, L09, L01 |
| Push security (JWT/JWKS, SSRF) | `push:security` | L08 |
| Durable task store | `taskstore:db` | L08 (L00 exercise) |
| Decision model (Nimble) | `decision` | L04, L05, L06, L07, L09, L10 |
| OTel traces / logs / metrics | `otel:traces`, `otel:logs`, `otel:metrics` | all (tour in the root notebook) |

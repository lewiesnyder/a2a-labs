# Appendix E: Sources and References

These are the primary references the spec was built from. Coding agents MUST prefer them over secondary blogs when resolving [VERIFY] items.

## A2A protocol
- Specification (latest, v1.0): https://a2a-protocol.org/latest/specification/
- What's new in v1.0: https://a2a-protocol.org/latest/whats-new-v1/
- Releases: https://github.com/a2aproject/A2A/releases
- Extensions topic: https://github.com/a2aproject/A2A/blob/main/docs/topics/extensions.md
- Python tutorial (server, streaming, multi-turn): https://a2a-protocol.org/latest/tutorials/python/5-start-server/ and https://a2a-protocol.org/latest/tutorials/python/7-streaming-and-multiturn/
- Python SDK API reference (client): https://a2a-protocol.org/latest/sdk/python/api/a2a.client.html

## a2a-python SDK
- PyPI: https://pypi.org/project/a2a-sdk/
- v1.0 migration guide: https://github.com/a2aproject/a2a-python/blob/main/docs/migrations/v1_0/README.md
- Samples (`hello_world_agent.py`, `cli.py`): https://github.com/a2aproject/a2a-python/tree/main/samples
- Issue #1344 (A2A-Version query param), #742 (compat limitations)

## Samples, tools, conformance
- a2a-samples: https://github.com/a2aproject/a2a-samples
  - LangGraph currency agent (push and auth): https://github.com/a2aproject/a2a-samples/blob/main/samples/python/agents/langgraph/README.md
  - AGP extension spec: https://github.com/a2aproject/a2a-samples/blob/main/extensions/agp/spec.md
  - Issues #309 (astream) and #292 (subscripting stream responses)
- A2A Inspector: https://github.com/a2aproject/a2a-inspector
- A2A TCK: https://github.com/a2aproject/a2a-tck
- AAIF builder guide (v1.0 migration, security, production): https://aaif.io/blog/a2a-v1-0-a-builder-s-guide-part-2-migration-security-and-production

## Google ADK
- PyPI: https://pypi.org/project/google-adk/
- Releases: https://github.com/google/adk-python/releases
- Exposing over A2A: https://google.github.io/adk-docs/a2a/quickstart-exposing/
- Consuming over A2A: https://adk.dev/a2a/quickstart-consuming/
- ADK A2A extension: https://adk.dev/a2a/a2a-extension/
- `to_a2a` source: https://github.com/google/adk-python/blob/main/src/google/adk/a2a/utils/agent_to_a2a.py
- Collaboration modes (chat, task, single_turn): https://adk.dev/workflows/collaboration/
- Ollama sample: https://github.com/google/adk-python/blob/main/contributing/samples/hello_world_ollama/README.md
- Issues #7334 (`use_legacy` on a2a-sdk 1.x) and #7247 (duplicate replies)

## Strands Agents
- PyPI: https://pypi.org/project/strands-agents/
- A2A concepts: https://strandsagents.com/docs/user-guide/concepts/multi-agent/agent-to-agent/
- A2AServer configuration: https://strandsagents.com/docs/user-guide/sdk/multi-agent/a2a-server-configuration/
- API, A2AServer: https://strandsagents.com/docs/api/python/strands.multiagent.a2a.server/
- API, A2AAgent: https://strandsagents.com/docs/api/python/strands.agent.a2a_agent/
- API, `make_a2a_client`: https://strandsagents.com/docs/api/python/strands.vended_tools.a2a_client.a2a_client/
- Graph: https://strandsagents.com/docs/user-guide/concepts/multi-agent/graph/
- v1.25.0 changelog (A2AAgent): https://strandsagents.com/changelog/sdk/python-v1.25.0/

## LangChain / LangGraph
- LangSmith / Agent Server A2A endpoint: https://docs.langchain.com/langsmith/server-a2a
- LangGraph docs (interrupts, checkpointers, streaming): https://docs.langchain.com/oss/python/langgraph/

## OpenTelemetry
- GenAI agent spans: https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md
- OTel Python: https://opentelemetry.io/docs/languages/python/
- grafana/otel-lgtm: https://github.com/grafana/docker-otel-lgtm

## Ollama models
- qwen3.8: https://ollama.com/library/qwen3.8
- nimble-v3 (decision model, `/v1/systemone`): https://ollama.com/library/nimble-v3
- llama3.2: https://ollama.com/library/llama3.2

## Requester-provided material
- "Architectural Deep Dive: Google ADK, A2A, and Cross-Framework Task Delegation in Python" (Gemini research, supplied by the requester). Corrections are recorded in `10-pitfalls-and-corrections.md` §7.

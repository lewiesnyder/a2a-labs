# 05: Observability (OpenTelemetry)

**Goal:** every lab request produces one connected **trace** that starts in the notebook cell and spans every A2A hop, framework agent, LLM call, tool call, MCP call, decision-model call and push delivery. **Logs** are correlated with that trace, and **metrics** describe A2A and GenAI behavior. All three signals land in Grafana via `grafana/otel-lgtm`.

## 1. Architecture

```text
 notebook (service.name=notebook-labNN) ──traceparent──► A2A server (Starlette/FastAPI instrumented)
      │                                                     │ executor span → framework spans (ADK/Strands/LangChain)
      │                                                     │       └─ httpx → Ollama (/api/chat, /v1/systemone)
      │                                                     └─ A2A client (httpx instrumented + interceptor) ──► next agent ...
      ▼
   OTLP gRPC :4317  ───────────────►  grafana/otel-lgtm  (Collector → Tempo | Loki | Prometheus → Grafana :3000)
```

- Exporter: OTLP gRPC to `OTEL_EXPORTER_OTLP_ENDPOINT` (default `http://lgtm:4317` in docker, `http://localhost:4317` on host).
- Optional profile `collector`: a separate `otel/opentelemetry-collector-contrib` instance in front of lgtm. It demonstrates processors (batch, attributes, tail sampling) in a production-shaped setup. Not required by any lab.

## 2. `init_telemetry()` contract

```python
def init_telemetry(
    service_name: str,                       # e.g. "lab02-strands-weather"
    *, framework: Literal["a2a-sdk","adk","strands","langgraph","mcp","notebook","service"],
    lab_id: str | None = None,               # default settings().lab_id
    enable_metrics: bool = True,
    enable_logs: bool = True,
    instrument_httpx: bool = True,
    instrument_asgi_app: Starlette | FastAPI | None = None,
) -> TelemetryHandles
```

Behavior, in order:

1. **Resource:** `service.name`, `service.namespace="a2a-labs"`, `service.version` (package version), `deployment.environment=$A2A_LABS_ENV` (default `lab`), `a2a_labs.lab_id`, `a2a_labs.framework`, and `host.name`.
2. **Traces:** `TracerProvider(resource, sampler=ParentBased(ALWAYS_ON))` with a `BatchSpanProcessor` around `OTLPSpanExporter`. Set it as the global provider. Idempotent: a second call returns the existing handles.
3. **Metrics:** `MeterProvider` with a `PeriodicExportingMetricReader(OTLPMetricExporter, 10s)`, set globally. Optional: disabled when `OTEL_METRICS_EXPORTER=none` or `enable_metrics=False`.
4. **Logs:** `LoggerProvider` with a `BatchLogRecordProcessor(OTLPLogExporter)`. Attach an `opentelemetry.sdk._logs.LoggingHandler` to the root logger at `LOG_LEVEL` (default INFO). Also install a console handler with the format `%(asctime)s %(levelname)s [%(name)s] [trace=%(otelTraceID)s span=%(otelSpanID)s] %(message)s` via `LoggingInstrumentor(set_logging_format=False)`, which injects the trace and span IDs.
5. **Propagators:** W3C TraceContext + Baggage (the defaults). Explicitly set with `set_global_textmap(CompositePropagator([...]))`.
6. **Auto-instrumentation:**
   - `HTTPXClientInstrumentor().instrument()` covers A2A clients, Ollama calls and webhook POSTs.
   - `StarletteInstrumentor.instrument_app(app)` or `FastAPIInstrumentor`.
   - `GrpcInstrumentorClient/Server` (Lab 00).
   - `framework == "langgraph"`: `LangChainInstrumentor().instrument(tracer_provider=…)` (OpenInference).
   - `framework == "strands"`: `StrandsTelemetry(tracer_provider=<global>)` [VERIFY constructor accepts provider; otherwise call `StrandsTelemetry()` *after* the global provider is set and do NOT call `setup_otlp_exporter()` twice]. Set `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`.
   - `framework == "adk"`: ADK emits OTel spans natively through the global provider (and `gen_ai.client.*` metrics in ADK ≥2.2). Verify no duplicate exporter setup [VERIFY ADK telemetry env flags].
   - `a2a-sdk[telemetry]`: SDK spans from `a2a.utils.telemetry` decorators appear automatically once a global provider exists.
7. **Shutdown:** `atexit` flushes all providers. Notebooks call `handles.flush()` at the end of each lab section, so traces appear before the learner opens Grafana.

Rule: **`init_telemetry()` is the first executable line of every `main()` and of every notebook's setup cell**, before any framework import that could cache a no-op tracer. Frameworks like ADK and Strands obtain tracers at import time [VERIFY]. For safety, server modules import framework packages inside `main()` after telemetry init, or rely on proxy tracers, which the OTel API supports.

## 3. Span model

Spans emitted by this repo's code. Framework and auto-instrumented spans add to these.

| Span name | Kind | Emitted by | Key attributes |
|---|---|---|---|
| `notebook.cell <title>` | INTERNAL | `display.cell_span("title")` context manager in notebooks | `a2a_labs.lab_id`, `a2a_labs.cell` |
| `a2a.client <Method>` (e.g. `a2a.client SendStreamingMessage`) | CLIENT | `TelemetryInterceptor` | `rpc.system="a2a"`, `rpc.method`, `a2a.protocol.version`, `a2a.binding`, `a2a.agent.name`, `server.address`, `a2a.task.id`, `a2a.context.id`, `a2a.task.state` (final), `a2a.response.kind` (`message`/`task`), `a2a.extensions`, `gen_ai.operation.name="invoke_agent"`, `gen_ai.agent.name`, `gen_ai.conversation.id`=context_id |
| `invoke_agent <agent>` | CLIENT for remote / INTERNAL for local | framework instrumentation or our interceptor (when the framework emits none) | GenAI semconv |
| `a2a.server.execute` | SERVER-side INTERNAL | `TracedExecutor.execute` | `a2a.task.id`, `a2a.context.id`, `a2a.agent.name`, `a2a.user_input.chars`, `a2a.task.final_state` |
| `a2a.server.cancel` | INTERNAL | `TracedExecutor.cancel` | `a2a.task.id` |
| span **events** `a2a.task.state_change` | — | EventQueue tap | `from`, `to`, `message` (truncated to 200 chars) |
| span **events** `a2a.artifact.chunk` | — | EventQueue tap | `artifact_id`, `append`, `last_chunk`, `bytes` |
| `chat <model>` | CLIENT | framework/OpenInference/Strands/ADK | `gen_ai.provider.name="ollama"`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` |
| `execute_tool <tool>` | INTERNAL | framework | `gen_ai.tool.name`, `gen_ai.tool.call.id` |
| `decision.decide` | CLIENT | `DecisionModel` | `gen_ai.request.model`, `decision.backend`, `decision.questions`, `decision.<q>.choice`, `decision.<q>.p`, `decision.<q>.score` |
| `mcp.call_tool <tool>` | CLIENT/SERVER | Lab 03 wrappers (if MCP SDK lacks OTel [VERIFY]) | `mcp.tool.name`, `mcp.session.id` |
| `a2a.push.deliver` | CLIENT | push sender wrapper (Lab 08) | `a2a.task.id`, `url.full` (redacted query), `http.response.status_code`, `a2a.push.config.id` |
| `a2a.push.receive` | SERVER | webhook-receiver | `a2a.task.id`, `a2a.push.jwt.valid`, `a2a.push.token.valid`, `a2a.task.state` |
| `registry.lookup` / `registry.verify_card` | INTERNAL | Lab 06 | `registry.candidates`, `a2a.card.signature.valid`, `a2a.card.kid` |
| `agp.route` | INTERNAL | Lab 09 gateway | `agp.capability`, `agp.route.path`, `agp.route.cost`, `agp.policy.violations` |

Sensitive content capture: prompt and response content on GenAI spans is **off by default**. Turn it on with `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`, which is set to `true` in `.env.example` because this is a lab environment. Notebook 00 explains the trade-off.

### 3.1 Client interceptor (`telemetry/a2a_client.py`)

- Implements the `a2a.client` interceptor interface (`ClientCallInterceptor` [VERIFY name/signature in 1.x]). On each call it:
  1. starts a span `a2a.client <Method>`;
  2. injects headers (`traceparent` comes via httpx instrumentation anyway, but the interceptor guarantees it for gRPC metadata);
  3. records duration and errors (sets span status ERROR with the JSON-RPC error code as `a2a.error.code` and `error.type`).
- For streaming calls it wraps the async iterator. It records each event as a span event (`a2a.stream.event` with `kind`, `state`) and ends the span when the stream closes. It also records time-to-first-event (`a2a.client.ttfe` histogram).
- If the SDK interceptor API cannot wrap stream lifetimes, wrap at the helper level (`a2a_labs_common.a2a.client.stream_text`) and keep the interceptor for header injection only. Document which approach was used.

### 3.2 Server executor wrapper (`telemetry/a2a_server.py`)

```python
class TracedExecutor(AgentExecutor):
    def __init__(self, inner: AgentExecutor, agent_name: str): ...
    async def execute(self, context, event_queue):
        with tracer.start_as_current_span("a2a.server.execute", attributes=...) as span:
            tapped = EventQueueTap(event_queue, span, metrics)   # forwards everything, observes state/artifacts
            await self.inner.execute(context, tapped)
    async def cancel(self, context, event_queue): ...
```

- The `EventQueueTap` subclass/proxy MUST forward every method of `EventQueue` unchanged [VERIFY that EventQueue can be proxied; otherwise subscribe to a `tap()` child queue if the SDK provides one].
- Used for **pure a2a-sdk servers and LangGraph executors**. For ADK (`A2aAgentExecutor`) and Strands (`A2AServer`), wrap via `agent_executor_factory` (ADK) or the `A2AServer` executor hook [VERIFY]. If wrapping isn't possible, rely on framework spans plus Starlette server spans. The A2A attributes then come from a Starlette middleware that parses the JSON-RPC envelope (`method`, `params.message.taskId/contextId`): `A2AEnvelopeMiddleware`, marked `# CUSTOM`.

## 4. Logs

- Every service logs through stdlib `logging`, exported to Loki via OTLP and correlated by `trace_id`/`span_id`.
- Log events required in every A2A server, at INFO:
  - `card served`
  - `task created id=… context=…`
  - `task state id=… WORKING→COMPLETED`
  - `artifact emitted id=… chunks=…`
  - `task canceled`
  - `push delivered url=… status=…`
- WARN for JSON-RPC errors returned. ERROR for executor exceptions.
- Loki labels come from the resource (`service_name`, `service_namespace`). Lab dashboards filter on `service_namespace="a2a-labs"` and `a2a_labs_lab_id`.

## 5. Metrics (optional but implemented)

| Instrument | Type | Unit | Attributes | Emitted by |
|---|---|---|---|---|
| `a2a.client.requests` | Counter | `{request}` | `rpc.method`, `a2a.agent.name`, `outcome` (`ok`/`error`), `a2a.error.code` | client interceptor |
| `a2a.client.duration` | Histogram | `s` | `rpc.method`, `a2a.agent.name` | client interceptor |
| `a2a.client.ttfe` | Histogram | `s` | `a2a.agent.name` | streaming wrapper |
| `a2a.server.tasks.started` | Counter | `{task}` | `a2a.agent.name` | TracedExecutor |
| `a2a.server.task.duration` | Histogram | `s` | `a2a.agent.name`, `a2a.task.final_state` | TracedExecutor |
| `a2a.server.task.state_transitions` | Counter | `{transition}` | `from`, `to`, `a2a.agent.name` | EventQueueTap |
| `a2a.server.artifact.chunks` | Counter | `{chunk}` | `a2a.agent.name` | EventQueueTap |
| `a2a.push.deliveries` | Counter | `{delivery}` | `outcome`, `http.response.status_code` | push sender wrapper |
| `a2a.push.received` | Counter | `{notification}` | `valid` | webhook-receiver |
| `decision.duration` | Histogram | `s` | `decision.backend`, `gen_ai.request.model` | DecisionModel |
| `decision.choice` | Counter | `{decision}` | `question`, `choice` | DecisionModel |
| `gen_ai.client.token.usage` / `gen_ai.client.operation.duration` | Histogram | per semconv | per semconv | frameworks (ADK ≥2.2, Strands, OpenInference [VERIFY]) |

## 6. Grafana provisioning

`observability/grafana/provisioning/dashboards.yaml` mounts `observability/grafana/dashboards/` into the lgtm container at `/otel-lgtm/grafana/conf/provisioning/dashboards` [VERIFY path for the image version].

**A2A Labs Overview** dashboard, with variables `lab_id` and `service`:
- Requests per method (rate of `a2a.client.requests` by `rpc_method`).
- Error rate by `a2a_error_code`.
- p50/p95 `a2a.client.duration` per agent.
- Task final-state distribution (bar chart of `a2a.server.task.duration_count` by `a2a_task_final_state`).
- State transitions Sankey-like table (`from` → `to` counts).
- Push deliveries ok/failed.
- Decision choices per question (Nimble).
- Token usage by model.
- Logs panel (Loki, `{service_namespace="a2a-labs"} | a2a_labs_lab_id="$lab_id"`).
- Traces panel (Tempo search `resource.service.namespace="a2a-labs"`).

**A2A Task Lifecycle** dashboard: input `task_id`. Shows the Tempo TraceQL search `{ span.a2a.task.id = "$task_id" }`, related logs and the state-change events table.

## 7. Notebook integration

- Setup cell: `tel = init_telemetry("notebook-labNN", framework="notebook")`.
- Each major demo is wrapped in `with display.cell_span("Send streaming message") as span:`. After it finishes, `display.trace_link(span)` prints a clickable Grafana link.
- Notebook `00-setup-and-observability.ipynb` tours the three signals with a TraceQL example, a LogQL example and a PromQL example.

## 8. Acceptance criteria (observability)

1. Running any lab notebook top to bottom yields ≥1 trace in Tempo where the root span is `notebook.cell …` and the trace contains spans from **every** service the lab touches. Verified by `tests/e2e/test_trace_connectivity.py`, which queries the Tempo API by trace ID.
2. Every log line emitted during an A2A request carries a non-zero `trace_id`.
3. `a2a.client.requests` shows non-zero series for every method exercised by Lab 00.
4. Disabling telemetry (`A2A_LABS_OTEL_ENABLED=false`) yields no exporter errors and no behavior change.

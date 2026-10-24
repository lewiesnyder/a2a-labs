# 03: Shared Library `a2a-labs-common`

Package `libs/a2a-labs-common`, import name `a2a_labs_common`. Its job is to keep lab code short and focused on the pattern being taught. It MUST stay thin: it wraps official constructs and never replaces them.

```text
src/a2a_labs_common/
├── __init__.py
├── config.py            # Settings (pydantic-settings), endpoint resolution, lab metadata
├── models/
│   ├── __init__.py      # public API: get_chat_model, get_decision_model, ModelRole
│   ├── settings.py      # loads config/models.yaml + env overrides
│   ├── langchain.py     # role → BaseChatModel
│   ├── adk.py           # role → ADK model (LiteLlm or str)
│   ├── strands.py       # role → Strands Model
│   └── decision.py      # DecisionModel protocol + Nimble/systemone + LLM fallback
├── telemetry/
│   ├── __init__.py      # init_telemetry, get_tracer, get_meter
│   ├── setup.py         # providers, exporters, instrumentors, logging bridge
│   ├── a2a_client.py    # client interceptor: spans + metrics per A2A call
│   ├── a2a_server.py    # executor wrapper + EventQueue tap for task-state spans/metrics
│   ├── metrics.py       # metric instrument definitions (05-observability.md §5)
│   └── frameworks.py    # framework-specific enablement (Strands, ADK, LangChain)
├── a2a/
│   ├── __init__.py
│   ├── cards.py         # build_card(...) helper, interface builders, card validation
│   ├── server.py        # build_a2a_app(...) for pure a2a-sdk servers (JSON-RPC/REST/gRPC)
│   ├── client.py        # connect(...), send_text(...), stream_text(...), wait_for_terminal(...)
│   ├── events.py        # StreamResponse helpers: kind(), text(), state(), pretty()
│   ├── states.py        # TERMINAL, INTERRUPTED sets; is_terminal(); state_name()
│   ├── ids.py           # new_message_id(), new_context_id() (UUID4), derived_context_id(parent, name) (uuid5)
│   ├── methods.py       # METHOD_CROSSWALK constant (single source for 01 §2 table)
│   ├── langgraph.py     # LangGraphA2AExecutor (Lab 01 §2.3), RemoteA2ANode (Lab 04 §2.2)
│   ├── langchain_tool.py# a2a_agent_as_langchain_tool (Lab 02 §3.3)
│   ├── trust.py         # CardVerifier, VerifiedCardResolver, TrustStore, sign_card (Lab 06 §4)
│   ├── push.py          # JwtPushNotificationSender, ValidatingPushConfigStore, jwks route (Lab 08 §2)
│   └── relay.py         # RelayExecutor (Lab 07 §4)
├── auth.py              # BearerAuthMiddleware, ApiKeyMiddleware, OAuthClientCredentialsAuth (Lab 10)
├── agp/                 # AGP models, table, gateway executor (Lab 09 §3) — EXPERIMENTAL
├── display.py           # rich renderers for notebooks: cards, events, tasks, tables, mermaid
└── testing.py           # in-process ASGI client fixtures, fake chat model, fake decision model
```

## 1. `config.py`

```python
class LabSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    runtime: Literal["docker", "host"] = Field("host", alias="A2A_LABS_RUNTIME")
    config_dir: Path = Field(Path("config"), alias="A2A_LABS_CONFIG_DIR")
    lab_id: str = Field("unknown", alias="A2A_LABS_LAB_ID")          # e.g. "lab02"
    a2a_version: str = Field("1.0", alias="A2A_LABS_A2A_VERSION")
    enable_v03_compat: bool = Field(True, alias="A2A_LABS_V03_COMPAT")
    otel_enabled: bool = Field(True, alias="A2A_LABS_OTEL_ENABLED")

def settings() -> LabSettings: ...                    # cached
def endpoint(name: str) -> str: ...                   # 02-repository-structure.md §4
def port(name: str) -> int: ...
def public_url(name: str) -> str: ...                 # URL to advertise on the card (docker_url)
```

`config_dir` lookup order: the env var, then walk up from CWD until `config/endpoints.yaml` is found. This makes notebooks work from any directory.

## 2. `a2a/cards.py`

```python
def build_card(
    *, service: str, name: str, description: str, version: str = "1.0.0",
    skills: list[AgentSkill], streaming: bool = True, push_notifications: bool = False,
    extended_agent_card: bool = False, extensions: list[AgentExtension] | None = None,
    bindings: Sequence[Literal["JSONRPC", "HTTP+JSON", "GRPC"]] = ("JSONRPC",),
    input_modes: Sequence[str] = ("text/plain",), output_modes: Sequence[str] = ("text/plain",),
    security_schemes: dict[str, SecurityScheme] | None = None,
    security_requirements: list[...] | None = None,
    provider: AgentProvider | None = AgentProvider(organization="A2A Labs", url="https://github.com/<org>/a2a-labs"),
) -> AgentCard:
    """Builds a v1.0 AgentCard with supported_interfaces derived from config/endpoints.yaml.
    JSONRPC → public_url(service); HTTP+JSON → public_url(service)+'/rest'; GRPC → public_url(f'{service}-grpc')."""

def skill(id: str, name: str, description: str, tags: list[str], examples: list[str],
          input_modes: list[str] | None = None, output_modes: list[str] | None = None) -> AgentSkill: ...

def validate_card(card: AgentCard) -> list[str]:
    """Returns human-readable problems: missing interfaces, duplicate skill ids, streaming flag
    inconsistent with server, no skills, description too short (<20 chars). Used in tests."""
```

ADK and Strands generate their own cards. Labs MAY pass a `build_card(...)` result to `to_a2a(agent_card=...)`, and SHOULD do so wherever a deterministic card is needed, such as Labs 05, 06 and 09. Strands builds its card from `name`, `description` and `skills` [VERIFY whether `A2AServer` accepts a full card]. In both cases, `test_cards.py` fetches the live card and runs `validate_card`.

## 3. `a2a/server.py`: pure a2a-sdk server builder

```python
def build_a2a_app(
    *, card: AgentCard, executor: AgentExecutor,
    task_store: TaskStore | None = None,                    # default InMemoryTaskStore()
    push: bool = False,                                     # wire push store + sender
    push_config_store: PushNotificationConfigStore | None = None,
    push_sender_factory: Callable[[httpx.AsyncClient, PushNotificationConfigStore], PushNotificationSender] | None = None,
    extended_card: AgentCard | None = None,                 # served via GetExtendedAgentCard
    rest: bool = False, rest_prefix: str = "/rest",
    enable_v0_3_compat: bool | None = None,                 # default from settings
    middleware: Sequence[Middleware] = (),
) -> Starlette:
    """Builds the app with the official route factories:
       create_agent_card_routes(card), create_jsonrpc_routes(handler, rpc_url='/', enable_v0_3_compat=…),
       create_rest_routes(handler, enable_v0_3_compat=…) mounted at rest_prefix [VERIFY mountability].
       Wraps the executor with telemetry.a2a_server.TracedExecutor.
       Instruments the app with OTel Starlette instrumentation."""

async def serve_grpc(*, card: AgentCard, handler: RequestHandler, port: int) -> None:
    """Starts the SDK gRPC server for the same handler (Lab 00 only) [VERIFY a2a.server.request_handlers grpc API]."""

def run(app: Starlette, *, service: str) -> None:
    """uvicorn.run(app, host='0.0.0.0', port=port(service), log_config=None)  # logging via OTel bridge"""
```

The `DefaultRequestHandler` constructor is `DefaultRequestHandler(agent_executor=, task_store=, agent_card=, push_config_store=, push_sender=)` [VERIFY 1.x keywords, particularly the extended-card argument].

## 4. `a2a/client.py`: client helpers (thin over `a2a.client`)

```python
@dataclass
class Connected:
    name: str
    card: AgentCard
    client: Client                  # a2a.client.Client
    httpx_client: httpx.AsyncClient

async def connect(
    name_or_url: str, *, streaming: bool = True, push_config: TaskPushNotificationConfig | None = None,
    headers: dict[str, str] | None = None, extensions: Sequence[str] = (),
    auth: CredentialService | None = None, transport: Literal["JSONRPC", "HTTP+JSON", "GRPC"] | None = None,
    a2a_version: str | None = None,
) -> Connected:
    """1) resolve card with A2ACardResolver(httpx_client, base_url)
       2) (host runtime) rewrite interface URLs to host URLs  # CUSTOM: host/docker URL bridging
       3) client = await create_client(card, client_config=ClientConfig(streaming=…, push_notification_config=…, ...))
       4) client.add_interceptor(TelemetryInterceptor(agent_name=card.name))
       5) add AuthInterceptor(auth) if provided
       Headers: always 'A2A-Version'; 'A2A-Extensions' if extensions."""

async def send_text(c: Connected, text: str, *, context_id: str | None = None, task_id: str | None = None,
                    reference_task_ids: Sequence[str] = (), metadata: dict | None = None,
                    data: dict | None = None, return_immediately: bool = False,
                    history_length: int | None = None) -> SendResult: ...
async def stream_text(c: Connected, text: str, **kw) -> AsyncIterator[StreamResponse]: ...
async def wait_for_terminal(c: Connected, task_id: str, *, poll_interval: float = 1.0, timeout: float = 300) -> Task: ...
async def wait_for_cards(services: Sequence[str], *, timeout: float = 120) -> None:
    """Polls each service's agent card; on timeout prints which are down and the `make up LAB=…` hint, then raises."""
def result_text(result: Task | Message) -> str:
    """Concatenates artifact text (Task) or message text (Message) using a2a.helpers.get_*_text."""

@dataclass
class SendResult:
    kind: Literal["message", "task"]
    message: Message | None
    task: Task | None
    events: list[StreamResponse]       # all raw events, for display
    @property
    def context_id(self) -> str: ...
    @property
    def task_id(self) -> str | None: ...
    @property
    def state(self) -> TaskState | None: ...
    @property
    def text(self) -> str: ...
```

Rules:
- Helpers MUST use `a2a.helpers.new_text_message` and the other `a2a.helpers` builders. They never build raw dicts.
- `connect()` resources are closed by `await c.aclose()` or `async with connect(...) as c:`.
- Notebooks MAY call the raw SDK directly. Lab 00 does, on purpose, and uses the helpers only after showing the raw form.

## 5. `a2a/events.py` and `states.py`

```python
TERMINAL = {TASK_STATE_COMPLETED, TASK_STATE_FAILED, TASK_STATE_CANCELED, TASK_STATE_REJECTED}
INTERRUPTED = {TASK_STATE_INPUT_REQUIRED, TASK_STATE_AUTH_REQUIRED}
def kind(ev: StreamResponse) -> Literal["task", "message", "status_update", "artifact_update"]
def state_name(s: TaskState) -> str            # "WORKING"
def event_text(ev: StreamResponse) -> str      # uses a2a.helpers.get_stream_response_text
```

## 6. `display.py` (notebook UX)

- `show_models()`: a table of role, provider, model and base URL (from `describe_models()`).
- `table(rows: list[dict])`, `compare_cards(cards)`, `state_name(state)`: small rendering helpers.
- `cell_span(title)`: a context manager that starts a `notebook.cell <title>` span (05 §3).
- `show_card(card)`: a rich panel showing name, description, interfaces table, capabilities checkboxes, skills table and extensions.
- `show_event(ev)`: a single colored line, e.g. `[12:01:03.120] status_update  task=ab12… WORKING  "step 2/5"`.
- `show_task(task)`: state badge, history count and artifacts with text previews.
- `show_wire(request_json, response_json)`: side-by-side JSON (raw wire view, used in Lab 00).
- `mermaid(src)`: renders Mermaid in Jupyter via `IPython.display.Markdown` with a mermaid block. JupyterLab 4.1+ renders this natively.
- `trace_link(span_or_trace_id)`: prints a Grafana Explore URL for Tempo with the trace ID: `http://localhost:3000/explore?...`.
- `crosswalk_table()`: renders the method crosswalk from `01-protocol-reference.md` §2 as a table. It is the single source of truth for that table and comes from a constant in `a2a_labs_common.a2a.methods`.

## 7. `testing.py`

- `asgi_client(app)`: an `httpx.AsyncClient(transport=ASGITransport(app))` plus `create_client` against it, so executors can be tested in-process without ports.
- `FakeChatModel` per framework, for deterministic unit tests:
  - LangChain: `GenericFakeChatModel`.
  - Strands: a minimal `Model` subclass returning scripted messages [VERIFY Model interface].
  - ADK: a `BaseLlm` subclass returning scripted `LlmResponse` [VERIFY].
- `FakeDecisionModel(answers: dict)`: returns fixed decisions.
- Pytest markers: `compose` (requires running services), `llm` (requires a real model), `slow`.

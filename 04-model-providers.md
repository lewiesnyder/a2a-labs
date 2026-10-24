# 04: Model Providers and Roles

## 1. Roles

Lab code never names a model. It asks for a **role**:

| Role | Interface | Default provider/model | Characteristics | Typical use |
|---|---|---|---|---|
| `reasoning` | Chat (tools, thinking) | `ollama` / `qwen3.8:27b` | 27B, 256K context, tools + vision + thinking (thinking ON by default), ~18 GB | Supervisors, planners, writers, multi-tool agents |
| `quick` | Chat (tools) | `ollama` / `llama3.2:latest` | 3B, fast, tool calling | Simple specialists, editors, summaries, notebook demos |
| `decision` | **Decision** (`/v1/systemone`) | `ollama_systemone` / `nimble-v3:latest` | 9B Bespoke Labs fine-tune of Qwen3.5-9B. Returns probabilities over predefined answers. **No chat, no tools, text-only, 32K context.** CC BY-NC 4.0. Needs Ollama ≥ 0.40.4. | Routing (conditional edges), guards, quality scores, policy checks |

> **License note.** `nimble-v3` is licensed for **noncommercial use only** (CC BY-NC 4.0). The repo README and `04` docs MUST state this. The `decision` role MUST stay swappable to the LLM fallback (see §4.3).

## 2. Configuration: `config/models.yaml`

```yaml
defaults:
  provider: ollama
  base_url: ${OLLAMA_BASE_URL:-http://localhost:11434}

roles:
  reasoning:
    provider: ${REASONING_PROVIDER:-ollama}
    model: ${REASONING_MODEL:-qwen3.8:27b}
    temperature: 0.2
    max_tokens: 4096
    think: ${REASONING_THINK:-true}        # qwen3.8 thinking; set false for low-latency demos
    reasoning_effort: ${REASONING_EFFORT:-medium}   # passed only when the provider supports it
    num_ctx: 32768                         # Ollama option; keep memory reasonable
  quick:
    provider: ${QUICK_PROVIDER:-ollama}
    model: ${QUICK_MODEL:-llama3.2:latest}
    temperature: 0.1
    max_tokens: 1024
    num_ctx: 8192
  decision:
    provider: ${DECISION_PROVIDER:-ollama_systemone}   # or: llm_fallback
    model: ${DECISION_MODEL:-nimble-v3:latest}
    fallback_role: quick                   # used when provider=llm_fallback or systemone unavailable
    timeout_s: 30

providers:
  ollama:          { base_url: ${OLLAMA_BASE_URL:-http://localhost:11434} }
  openai:          { api_key_env: OPENAI_API_KEY, base_url: ${OPENAI_BASE_URL:-} }
  anthropic:       { api_key_env: ANTHROPIC_API_KEY }
  gemini:          { api_key_env: GOOGLE_API_KEY }
  bedrock:         { region: ${AWS_REGION:-us-east-1} }
  openai_compatible: { base_url: ${OPENAI_COMPAT_BASE_URL:-}, api_key_env: OPENAI_COMPAT_API_KEY }  # vLLM, LM Studio, etc.
  ollama_systemone: { base_url: ${OLLAMA_BASE_URL:-http://localhost:11434} }
```

- `${VAR:-default}` interpolation is done by `models/settings.py`.
- Precedence: env var, then YAML, then built-in default.
- Compose sets `OLLAMA_BASE_URL=http://host.docker.internal:11434` by default, or `http://ollama:11434` with the container profile.

## 3. Framework adapters

Public API (`a2a_labs_common.models`):

```python
ModelRole = Literal["reasoning", "quick", "decision"]

def get_langchain_model(role: ModelRole = "quick", **overrides) -> BaseChatModel
def get_adk_model(role: ModelRole = "quick", **overrides) -> LiteLlm | str
def get_strands_model(role: ModelRole = "quick", **overrides) -> strands.models.Model
def get_decision_model() -> DecisionModel
def describe_models() -> list[dict]     # for the setup notebook table
```

Calling `get_*_model("decision")` for a chat adapter raises `ValueError("decision role is not a chat model; use get_decision_model()")`.

### 3.1 LangChain / LangGraph

| provider | construction |
|---|---|
| `ollama` | `ChatOllama(model=…, base_url=…, temperature=…, num_ctx=…, reasoning=think)` [VERIFY `reasoning` kwarg name in langchain-ollama for thinking] |
| `openai` / `openai_compatible` | `ChatOpenAI(model=…, base_url=…, api_key=…)` |
| `anthropic` | `ChatAnthropic(model=…)` |
| `gemini` | `ChatGoogleGenerativeAI(model=…)` |
| `bedrock` | `ChatBedrockConverse(model=…, region_name=…)` |

Fallback for unknown providers: `init_chat_model(model, model_provider=provider, **params)`.

### 3.2 Google ADK

- **Every ADK `Agent`/`LlmAgent` MUST set `model=` explicitly.** ADK ≥2.2 defaults to `gemini-3-flash-preview`.
- `ollama`: `LiteLlm(model=f"ollama_chat/{model}")` with env `OLLAMA_API_BASE=<base_url>`. Use the **`ollama_chat/` prefix, never `ollama/`.** ADK docs warn that `ollama/` causes infinite tool loops and lost context.
- `openai`: `LiteLlm(model=f"openai/{model}")`. `anthropic`: `LiteLlm(model=f"anthropic/{model}")`. `bedrock`: `LiteLlm(model=f"bedrock/{model}")`.
- `gemini`: the plain model string (native).
- Thinking: pass `extra_body`/`think` through LiteLLM only if supported [VERIFY]. Otherwise leave the model default.

### 3.3 Strands

- `ollama`: `OllamaModel(host=base_url, model_id=model, temperature=…, max_tokens=…, options={"num_ctx": …})`. **Always pass `model_id`** (required since 1.57.1). For `think` [VERIFY param, e.g. `additional_args={"think": …}`].
- `openai`: `OpenAIModel(client_args={"api_key": …, "base_url": …}, model_id=…)`. `anthropic`: `AnthropicModel(model_id=…)`. `bedrock`: `BedrockModel(model_id=…)`. `gemini`: `GeminiModel` [VERIFY]. Anything else: `LiteLLMModel(model_id=f"{provider}/{model}")`.

### 3.4 Tool-calling caveats with local models

- `llama3.2` (3B) handles one or two simple tools reliably, but **struggles with long tool lists**. Lab designs give quick-role agents **≤3 tools**.
- With thinking on, `qwen3.8:27b` adds latency. Supervisors and planners keep it on; demo cells that must be snappy set `REASONING_THINK=false`.
- Every LLM-backed lab cell MUST print which provider and model it is using: `display.show_models()`.

## 4. Decision model (Nimble / "system one")

### 4.1 Interface

```python
class Question(BaseModel):
    type: Literal["choice", "noul", "score"]
    instructions: str
    criteria: dict[str, str] | list[str] | dict = {}   # choice: {option: description}; score: [levels asc]; noul: {"true": "...", "false": "..."} or {}

class Answer(BaseModel):
    type: Literal["choice", "noul", "score"]
    choice: str | None = None          # choice
    noul: float | None = None          # P(true)
    score: float | None = None         # probability-weighted level index
    probabilities: dict[str, float] | list[float] | None = None
    legend: list[str] | None = None
    raw: dict                          # untouched backend payload

class DecisionResult(BaseModel):
    answers: dict[str, Answer]
    model: str
    latency_ms: float
    backend: Literal["ollama_systemone", "llm_fallback", "fake"]

class DecisionModel(Protocol):
    async def decide(self, state: str, questions: dict[str, Question]) -> DecisionResult: ...
    # Convenience wrappers
    async def choose(self, state: str, instructions: str, options: dict[str, str]) -> tuple[str, dict[str, float]]: ...
    async def check(self, state: str, instructions: str, threshold: float = 0.5) -> tuple[bool, float]: ...
    async def score(self, state: str, instructions: str, levels: list[str]) -> tuple[float, list[float]]: ...
```

### 4.2 `OllamaSystemOneDecisionModel`

`POST {base_url}/v1/systemone` with `Content-Type: application/json`. Request body:

```json
{
  "model": "nimble-v3:latest",
  "state": "<evidence text>",
  "questions": {
    "route": {
      "type": "choice",
      "instructions": "Which specialist should handle the user's request?",
      "criteria": {
        "currency": "Exchange rates or converting money between currencies",
        "weather": "Weather conditions or forecasts",
        "facts": "General knowledge questions",
        "none": "None of the specialists apply"
      }
    },
    "needs_clarification": {"type": "noul", "instructions": "Is the request missing information needed to act?", "criteria": {}},
    "urgency": {"type": "score", "instructions": "How urgent is the request?", "criteria": ["Not urgent", "Somewhat urgent", "Very urgent"]}
  }
}
```

Response: `{"answers": {"route": {"choice": "...", "probabilities": {...}}, "needs_clarification": {"noul": 0.12}, "urgency": {"score": 0.3, "probabilities": [...], "legend": [...]}}}`.

- The response shape beyond these fields is **not fully documented**. The parser MUST be tolerant: read known keys, keep the rest in `raw`.
- `scripts/check_models.py` MUST send this probe and print the raw response, so the builder can confirm the shape [VERIFY].
- Rules:
  - Always include a fallback option such as `none` or `other` in `choice` criteria.
  - Up to 64 questions per request. `state` plus questions must fit in 32K tokens.
  - Text only.
  - Use `httpx.AsyncClient` (instrumented, so the decision call shows as an HTTP child span) and wrap it in a `decision.decide` span (05-observability.md).

### 4.3 `LLMFallbackDecisionModel`

- Used when `DECISION_PROVIDER=llm_fallback`, or when the systemone endpoint returns 404 or connection errors and `DECISION_FALLBACK_ON_ERROR=true`.
- It prompts the `fallback_role` chat model (LangChain adapter) with structured output (`with_structured_output(<pydantic schema>)`), one call per request. The schema:
  - for `choice`: one of the option keys and a confidence;
  - for `noul`: a probability;
  - for `score`: a level index.
- It fills `Answer` with `backend="llm_fallback"`, so labs work with any provider.

### 4.4 Where Nimble is used (designed placements)

| Lab | Placement | Question(s) |
|---|---|---|
| 05 | **LangGraph supervisor conditional edge** `route_request` | `choice` over specialists (built dynamically from discovered Agent Card skills) + `none`. `noul` "is the request ambiguous?" leads to an interrupt or clarification. |
| 05 | Supervisor completion check | `noul` "Does the specialist's answer fully address the request?" Below 0.5, re-route once. |
| 06 | Registry delegation guard | `choice` picks the best verified agent from registry candidates (state = skill descriptions). |
| 07 | Editor gate in chain | `score` ["unusable", "needs major edits", "minor edits", "publishable"] on the writer's draft. Below 2, loop back to the writer (max 2 loops). |
| 09 | AGP gateway policy check | `noul` per policy constraint, e.g. "Does the payload contain PII?", before route selection. |
| 10 | Pre-flight request screening | `choice` among ["allow", "needs_auth", "reject"] to decide between REJECTED, AUTH_REQUIRED or proceed (demonstrates REJECTED driven by a model). |

These placements show the "system one / system two" split: fast, calibrated decisions from Nimble, with generation and tool use from the reasoning and quick models. Each placement MUST log the decision probabilities as span attributes (`decision.<question>.choice`, `decision.<question>.p`) so learners can see them in Grafana.

## 5. Verifying models: `scripts/check_models.py`

1. `GET {OLLAMA_BASE_URL}/api/version` and print the Ollama version. Warn if it is below 0.40.4 (needed for nimble-v3).
2. `GET /api/tags`. For each required tag (reasoning, quick, decision), report whether it is present. With `--pull`, `POST /api/pull` any missing tag and stream progress.
3. `POST /api/show` for each chat model and print its capabilities (`tools`, `thinking`, `vision`).
4. Smoke tests:
   - a chat completion with one tool for `quick` and `reasoning`;
   - the systemone probe from §4.2 for `decision`.
5. Exit non-zero on failure. The compose `models-check` one-shot service runs this script.

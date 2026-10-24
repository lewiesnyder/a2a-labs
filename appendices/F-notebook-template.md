# Appendix F: Notebook Template (jupytext percent format)

Author notebooks as `.py` in jupytext percent format, alongside the `.ipynb`, using `jupytext --sync` paired notebooks. Reviews then diff cleanly. The template below follows `07-notebook-standards.md`. It is shown filled in for the first steps of Lab 01.

```python
# %% [markdown]
# # Lab 01 — Direct Client → Agent
# **Goal:** talk to four agents built with four different stacks using the *same* A2A client code.
# ⏱ ~20 min · Prerequisites: Lab 00
#
# **What you'll learn**
# - How to resolve an **Agent Card** and read its interfaces, capabilities and skills
# - When an agent replies with a **Message** vs a server-owned **Task**
# - How `contextId` gives you multi-turn conversations in ADK, Strands and LangGraph
#
# Spec: [Agent Card §4.4](https://a2a-protocol.org/latest/specification/) · [SendMessage](https://a2a-protocol.org/latest/specification/)

# %% [markdown]
# ## 1. Architecture
# ```mermaid
# flowchart LR
#   NB[Notebook client] -->|A2A JSON-RPC| E[lab01-sdk-echo<br/>a2a-sdk]
#   NB --> A[lab01-adk-agent<br/>ADK to_a2a]
#   NB --> S[lab01-strands-agent<br/>Strands A2AServer]
#   NB --> L[lab01-langgraph-agent<br/>LangGraph executor]
#   A & S & L --> O[(Ollama · quick model)]
# ```
# | Service | Framework | Port | Replies with |
# |---|---|---|---|
# | lab01-sdk-echo | a2a-sdk | 10101 | Message |
# | lab01-adk-agent | ADK | 10102 | Task |
# | lab01-strands-agent | Strands | 10103 | Task |
# | lab01-langgraph-agent | LangGraph | 10104 | Task |

# %% tags=["parameters"]
QUICK_DEMO = False

# %% [markdown]
# ## 2. Setup
# Telemetry first, so every call below lands in one trace in Grafana.

# %%
# Initialise telemetry, show active models, and wait for the lab's services
from a2a_labs_common.telemetry import init_telemetry
from a2a_labs_common import display
from a2a_labs_common.a2a.client import connect, send_text, stream_text, wait_for_cards
from a2a_labs_common.a2a.ids import new_context_id

tel = init_telemetry("notebook-lab01", framework="notebook", lab_id="lab01")
display.show_models()
SERVICES = ["lab01-sdk-echo", "lab01-adk-agent", "lab01-strands-agent", "lab01-langgraph-agent"]
await wait_for_cards(SERVICES, timeout=120)   # prints: run `make up LAB=lab01` if this fails

# %% [markdown]
# ## 3. Concept: Message vs Task
# ... (≤250 words + sequenceDiagram) ...

# %% [markdown]
# ### Step 1 — Discover the four agents
# We fetch each `/.well-known/agent-card.json` and compare what each framework advertises.

# %% a2a_coverage=["op:Discovery", "pattern:1"]
# Resolve and display every Agent Card
agents = {name: await connect(name) for name in SERVICES}
for c in agents.values():
    display.show_card(c.card)
display.compare_cards([c.card for c in agents.values()])

# %% [markdown]
# **What just happened**
# - `connect()` used `A2ACardResolver` to fetch each card, then `create_client(card)` built a client
# - Every card lists `supported_interfaces` — the client picked JSON-RPC 1.0
# - ADK's card includes the ADK extension URI; Strands derived skills from its tools

# %% [markdown]
# ### Step 2 — Same question, four agents

# %% a2a_coverage=["op:SendMessage", "pattern:1", "state:COMPLETED"]
# Send the same request to every agent and compare the response kind
with display.cell_span("Same question to four agents") as span:
    rows = []
    for name, c in agents.items():
        r = await send_text(c, "Give me one travel tip for Lisbon")
        rows.append({"agent": name, "kind": r.kind, "state": r.state and display.state_name(r.state),
                     "text": r.text[:80]})
display.table(rows)
display.trace_link(span)

# %% [markdown]
# > 💡 **Tip** — `sdk-echo` returned a **Message**: no task was created, nothing to poll or cancel.
# > The others returned **Tasks** you can fetch later with `GetTask`.

# ... further steps ...

# %% [markdown]
# ## Exercises
# 1. (easy) ...

# %% tags=["solution"] jupyter={"source_hidden": true}
# Solution for exercise 1
...

# %% [markdown]
# ## Recap & next
# | Pattern | Use when | Avoid when |
# |---|---|---|
# | Direct client → agent | You know the agent and want its answer | You need routing across many agents → Lab 05 |

# %%
# Cleanup
for c in agents.values():
    await c.aclose()
tel.flush()
```

Cell metadata keys used: `tags` (`parameters`, `skip-ci`, `solution`) and `a2a_coverage` (Appendix A tags).

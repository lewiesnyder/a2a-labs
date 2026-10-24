# Lab 07: Sequential Chaining / Multi-hop (Pattern 6)

**Package:** `labs/lab07-sequential-chain` → import `lab07_chain`
**Profile:** `lab07`
**Models:** `reasoning` (planner, writer), `quick` (editor), `decision` (quality gate)
**Frameworks:** ADK (planner), Strands (writer), LangGraph (editor), a2a-sdk (orchestrator)

> **Pattern 6:** Planner → writer → editor, or peer hops. Artifacts and messages pass along the chain, and **each hop owns its own tasks**.

## 1. Learning objectives

1. Pass **Artifacts** from one hop into the next hop's **Message** as Parts. Text and DataPart are kept structured: the outline travels as JSON.
2. Link hops with **`reference_task_ids`**, so every downstream task records where its input came from (provenance).
3. Compare two topologies:
   - **Orchestrated (hub-and-spoke):** an orchestrator calls each hop in turn and owns the overall task.
   - **Relay (peer hops):** each hop forwards to the next, and the chain's route travels in message metadata.
4. Build the same chain with framework-native constructs: an ADK `SequentialAgent` of `RemoteA2aAgent`s, and a Strands `GraphBuilder` of `A2AAgent` nodes. Compare the wire traffic.
5. Add a **quality loop**: Nimble scores the editor's review, and a low score sends the draft back to the writer, in the same writer context, as a new task referencing the previous ones.
6. Stream chain progress to the end client as status updates from the orchestrator's single top-level task.

## 2. Services

| Service | Port | Framework | Input → Output |
|---|---|---|---|
| `lab07-adk-planner` | 10701 | ADK `to_a2a` (reasoning) | topic text → artifact `outline` (DataPart JSON `{title, audience, sections:[{heading, key_points[]}]}` + text rendering). Uses `output_schema` [VERIFY ADK structured output with LiteLLM/Ollama; fall back to JSON instruction + validation]. |
| `lab07-strands-writer` | 10702 | Strands `A2AServer` (reasoning) | outline DataPart (+ optional editor feedback) → artifact `draft` (text/markdown), streamed in chunks |
| `lab07-langgraph-editor` | 10703 | LangGraph executor (quick) | draft text → artifacts `final` (text/markdown) + `review` (DataPart `{issues[], summary}`) |
| `lab07-chain-orchestrator` | 10704 | a2a-sdk `AgentExecutor` (no LLM of its own; uses DecisionModel) | topic → runs the chain → artifacts `final`, `provenance` (DataPart with all hop task IDs and states) |

## 3. Orchestrated mode (`executors/chain_orchestrator.py`)

```text
client ──SendStreamingMessage──► orchestrator (Task T0, context C0)
  status WORKING "1/3 planning"      → planner:  SendMessage(text=topic, context=Cp)                     → task T1, artifact outline
  status WORKING "2/3 writing"       → writer:   SendMessage(parts=[data(outline)], reference_task_ids=[T1], context=Cw) → T2, draft (stream chunks relayed as WORKING status "writing… n chars")
  status WORKING "3/3 editing"       → editor:   SendMessage(parts=[text(draft)], reference_task_ids=[T2], context=Ce)  → T3, final + review
  decision.score(review, levels=[unusable, major, minor, publishable])
     if score < 2 and loops < MAX_LOOPS(2):
        status WORKING "revising (loop k)" → writer: SendMessage(parts=[data(outline), text(feedback)], reference_task_ids=[T2,T3], context=Cw) → T4 ...
  artifacts: final (from last editor), provenance {hops:[{agent, task_id, context_id, state, ms}], loops, scores}
  COMPLETED
```

Rules:
- Each hop gets **its own** remote `context_id` (Cp, Cw, Ce), derived via uuid5 from C0 and the hop name. The writer's context is reused in revision loops so it remembers earlier drafts.
- The orchestrator **never** forwards another hop's `task_id` to a different agent. Provenance travels only in `reference_task_ids` and the `provenance` artifact.
- Any hop that is FAILED or REJECTED makes the orchestrator FAILED, with a status message naming the hop. An INPUT_REQUIRED hop is surfaced as orchestrator INPUT_REQUIRED (planner asks "Who is the audience?" when the topic is too vague); the next client message is forwarded to that hop's task.
- **Cancel** of T0 propagates `CancelTask` to the active hop task.

## 4. Relay mode (`a2a_labs_common.a2a.relay`)

Request message metadata: `{"relay": {"route": ["lab07-strands-writer", "lab07-langgraph-editor"], "origin_task": "<T1>", "hop": 0}}`.

`RelayExecutor(inner_executor)` wraps each hop's executor [VERIFY wrapping for ADK/Strands. If they cannot be wrapped, run relay hops as thin a2a-sdk relay proxies `lab07-*-relay` in front of each agent (`# CUSTOM`)].
1. It runs the inner work and collects its artifacts.
2. If `route` is non-empty, it calls the next hop with its artifacts as parts. It pops itself from the route and sets `reference_task_ids=[own_task]`.
3. It streams downstream status as its own WORKING updates.
4. It attaches the downstream final artifacts to its own task as `downstream-final`.
5. Then it completes.

The client sees one task at the first hop; the chain completes behind it.

The notebook compares the two topologies:

| | Orchestrated | Relay |
|---|---|---|
| Who knows the route | Orchestrator | The message (metadata) |
| Failure handling | Central | Each hop |
| Coupling | Hops independent | Hops must support relay |
| Observability | One fan-out trace | A deep nested trace |
| Typical use | Workflows with QA loops | Pipelines across org boundaries |

## 5. Framework-native chains (notebook, in-process)

- **ADK:** `SequentialAgent(name="chain", sub_agents=[RemoteA2aAgent(planner), RemoteA2aAgent(writer), RemoteA2aAgent(editor)])`, run with an ADK `Runner` in the notebook. Show how ADK passes context: conversation history/events, not explicit artifacts. Inspect which parts actually travel on the wire to each remote, using the ADK extension. This illustrates implicit vs explicit hand-off.
- **Strands:** `GraphBuilder` with `A2AAgent` nodes and edges planner→writer→editor. Strands passes the previous node's output as the next node's input. Show the payloads.
- **LangGraph:** three `RemoteA2ANode`s in a linear graph (from Lab 04).

## 6. Notebook outline

1. Architecture: both topologies (Mermaid sequence diagrams).
2. Setup.
3. Concept: artifacts vs messages along a chain, `reference_task_ids`, and per-hop task ownership.
4. **Hop by hop manually**: call the planner, show the outline DataPart; call the writer with that DataPart and `reference_task_ids`, show the streamed draft; call the editor. This shows the raw mechanics before automation.
5. **Orchestrated chain**: stream from the orchestrator and watch hop progress. Show the `provenance` artifact table and fetch each hop's task with `GetTask` to verify the states.
6. **Quality loop**: the topic "Explain quantum entanglement to a 10-year-old in 3 paragraphs, using only words under 3 syllables" (hard to satisfy). Watch the Nimble scores and the revision loops, then show the writer's task history in its context (ListTasks by `context_id=Cw`).
7. **INPUT_REQUIRED mid-chain**: a vague topic ("Write about it") → the planner asks → the orchestrator relays → answer → continue.
8. **Cancel mid-chain**: cancel during writing, then verify the writer task is CANCELED as well.
9. **Relay mode**: the same topic and the comparison table. View the nested trace.
10. **Framework-native chains**: ADK `SequentialAgent` and Strands Graph. Compare the outputs and wire payloads.
11. Grafana: orchestrated vs relay trace shapes side by side.
12. Exercises:
    - (easy) add a "fact-checker" hop;
    - (medium) run the editor and a "SEO hop" in parallel after the writer;
    - (stretch) make relay mode resumable after a hop failure.

## 7. Acceptance tests

- The orchestrator produces `final` + `provenance`. Provenance lists 3+ hops with COMPLETED states and correct `reference_task_ids` chains (verified via GetTask on each hop).
- The quality loop runs at most `MAX_LOOPS`. With a scripted decision score of 1 it loops twice, then completes.
- INPUT_REQUIRED from the planner propagates and resumes.
- Cancel propagation: the active hop task becomes CANCELED.
- Relay mode yields the same final artifact type, and the first hop's task holds `downstream-final`.

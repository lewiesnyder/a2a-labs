"""Smoke test for the optional LangGraph Agent Server profile (``lab04-agentserver``).

Skipped unless the profile is running. It pins down what the built-in A2A endpoint really does
(langgraph-api 0.16.x), because it differs from both the spec and from our hand-built server.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.compose
BASE = "http://localhost:10403"
QUESTION = "Plan a trip from Chicago to Lisbon, 12-16 May, budget $1500, 2 travelers"


def _up() -> bool:
    try:
        return httpx.get(f"{BASE}/ok", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = [
    pytest.mark.compose,
    pytest.mark.skipif(not _up(), reason="profile lab04-agentserver is not running"),
]


@pytest.fixture(scope="module")
def url() -> str:
    # The A2A path takes the assistant's UUID, not the graph name `trip_planner`.
    found = httpx.post(f"{BASE}/assistants/search", json={"graph_id": "trip_planner"}).json()
    return f"{BASE}/a2a/{found[0]['assistant_id']}"


def rpc(url: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    r = httpx.post(
        url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=60
    )
    return r.json()  # type: ignore[no-any-return]


def message(text: str, **extra: Any) -> dict[str, Any]:
    return {
        "message": {
            "role": "user",
            "parts": [{"kind": "text", "text": text}],
            "messageId": str(uuid.uuid4()),
            "kind": "message",
            **extra,
        }
    }


def test_send_message_pauses_with_an_interrupt_artifact_then_resumes(url: str) -> None:
    ctx = str(uuid.uuid4())  # a UUID is mandatory
    first = rpc(url, "message/send", message(QUESTION, contextId=ctx))["result"]
    assert first["status"]["state"] == "input-required"  # v0.3 shape: hyphenated state, `kind`
    assert first["artifacts"][0]["name"] == "Interrupt"  # the interrupt is an artifact here
    assert "Approve?" in first["artifacts"][0]["parts"][0]["data"]["value"]["question"]
    done = rpc(
        url,
        "message/send",
        {
            "message": {
                "role": "user",
                "parts": [{"kind": "data", "data": {"resume": "yes"}}],
                "messageId": str(uuid.uuid4()),
                "kind": "message",
                "contextId": ctx,
                "taskId": first["id"],
            }
        },
    )
    assert done["result"]["status"]["state"] == "completed"


def test_documented_limits(url: str) -> None:
    assert rpc(url, "SubscribeToTask", {"id": "x"})["error"]["code"] == -32601
    assert rpc(url, "tasks/resubscribe", {"id": "x"})["error"]["code"] == -32601
    assert rpc(url, "tasks/pushNotificationConfig/set", {"taskId": "x"})["error"]["code"] == -32601
    real = rpc(url, "message/send", message(QUESTION, contextId=str(uuid.uuid4())))["result"]["id"]
    too_long = rpc(url, "tasks/get", {"id": real, "historyLength": 50})  # needs a REAL task id
    assert "historyLength cannot exceed 10" in too_long["error"]["message"]
    assert (
        rpc(url, "message/send", message("hi", contextId="not-a-uuid"))["error"]["code"] == -32602
    )
    assert httpx.post(f"{url}/v1/message:send", json={}).status_code == 404  # no REST binding


def test_card_advertises_streaming_but_no_push_and_a_wrong_host(url: str) -> None:
    aid = url.rsplit("/", 1)[1]
    card = httpx.get(f"{BASE}/.well-known/agent-card.json", params={"assistant_id": aid}).json()
    assert card["capabilities"]["streaming"] and not card["capabilities"]["pushNotifications"]
    assert card["supportedInterfaces"][0]["url"].startswith("http://0.0.0.0:")  # unusable as-is

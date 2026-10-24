"""Optional and required extensions, the activation echo and version negotiation."""

from __future__ import annotations

import pytest
from a2a.helpers import get_data_parts
from a2a.types import TaskState
from a2a.utils.errors import ExtensionSupportRequiredError, VersionNotSupportedError
from a2a_labs_common.a2a.client import connect, send_text
from lab10_secure.clients import card_json, message_params, post_json, rpc
from lab10_secure.data import BUDGET_EXT, TRACE_EXT

ASK = "What is the capital of Portugal?"


def artifact_ids(result) -> list[str]:
    return [a.artifact_id for a in result.task.artifacts]


# ── optional: trace-summary ──────────────────────────────────────────────────────────────────
async def test_the_trace_summary_artifact_exists_only_when_activated(stack) -> None:
    base = stack["lab10-ext-agent"].url
    async with await connect(base, streaming=False) as plain:
        off = await send_text(plain, ASK)
    async with await connect(base, streaming=False, extensions=[TRACE_EXT]) as on:
        res = await send_text(on, ASK)
    assert artifact_ids(off) == ["answer"] and "Lisbon" in off.text
    assert artifact_ids(res) == ["answer", "trace-summary"]
    summary = get_data_parts(res.task.artifacts[1].parts)[0]
    assert summary["spans"] == len(summary["steps"]) == 2 and summary["models"]
    assert list(res.task.artifacts[1].extensions) == [TRACE_EXT]  # tagged with its extension


async def test_the_server_echoes_what_it_activated_and_ignores_the_rest(stack) -> None:
    base = stack["lab10-ext-agent"].url
    body = message_params(ASK)
    off = rpc(base, "SendMessage", body)
    assert "a2a-extensions" not in off.headers
    on = rpc(base, "SendMessage", body, headers={"A2A-Extensions": TRACE_EXT})
    assert on.headers["a2a-extensions"] == TRACE_EXT
    # an extension the agent never declared is not activated, and not an error (unless required)
    other = "https://example.com/ext/unknown/v1"
    both = rpc(base, "SendMessage", body, headers={"A2A-Extensions": f"{TRACE_EXT}, {other}"})
    assert both.headers["a2a-extensions"] == TRACE_EXT
    assert "result" in both.json()


async def test_the_card_declares_the_extension_with_a_versioned_uri(stack) -> None:
    card = card_json(stack["lab10-ext-agent"].url)
    (ext,) = card["capabilities"]["extensions"]
    assert ext["uri"] == TRACE_EXT and ext["uri"].endswith("/v1")
    assert not ext.get("required")


# ── required: cost-budget ────────────────────────────────────────────────────────────────────
async def test_a_required_extension_that_is_missing_is_error_32008(stack) -> None:
    base = stack["lab10-required-ext-agent"].url
    r = rpc(base, "SendMessage", message_params("Summarize: A2A is neat"))
    err = r.json()["error"]
    assert err["code"] == -32008 and "cost-budget" in err["message"]
    assert err["data"][0]["reason"] == "EXTENSION_SUPPORT_REQUIRED"


async def test_the_sdk_client_raises_a_typed_error_for_the_missing_extension(stack) -> None:
    base = stack["lab10-required-ext-agent"].url
    async with await connect(base, streaming=False) as c:
        with pytest.raises(ExtensionSupportRequiredError, match="cost-budget"):
            await send_text(c, "Summarize: A2A is neat")


async def test_with_the_extension_and_budget_metadata_the_work_is_done(stack) -> None:
    base = stack["lab10-required-ext-agent"].url
    async with await connect(base, streaming=False, extensions=[BUDGET_EXT]) as c:
        res = await send_text(c, "Summarize: A2A is neat", metadata={BUDGET_EXT: {"max_usd": 0.05}})
    assert res.state == TaskState.TASK_STATE_COMPLETED and "A2A is neat" in res.text
    cost = get_data_parts(res.task.artifacts[1].parts)[0]
    assert cost == {"spent_usd": 0.01, "max_usd": 0.05, "currency": "USD"}


async def test_work_above_the_budget_is_rejected_not_failed(stack) -> None:
    base = stack["lab10-required-ext-agent"].url
    async with await connect(base, streaming=False, extensions=[BUDGET_EXT]) as c:
        res = await send_text(
            c, "Write a full report: A2A", metadata={BUDGET_EXT: {"max_usd": 0.05}}
        )
    assert res.state == TaskState.TASK_STATE_REJECTED
    assert get_data_parts(res.task.status.message.parts)[0] == {"price_usd": 0.08, "max_usd": 0.05}


async def test_activating_the_extension_without_its_metadata_is_invalid_params(stack) -> None:
    base = stack["lab10-required-ext-agent"].url
    r = rpc(
        base, "SendMessage", message_params("Summarize: hi"), headers={"A2A-Extensions": BUDGET_EXT}
    )
    assert r.json()["error"]["code"] == -32602


# ── version negotiation and multi-interface cards ────────────────────────────────────────────
async def test_the_card_lists_both_protocol_versions(stack) -> None:
    card = card_json(stack["lab10-ext-agent"].url)
    versions = {i["protocolVersion"]: i["url"] for i in card["supportedInterfaces"]}
    assert set(versions) == {"1.0", "0.3"} and versions["0.3"].endswith("/v03")


async def test_a_1_0_client_takes_the_first_interface(stack) -> None:
    async with await connect(stack["lab10-ext-agent"].url, streaming=False) as c:
        first = c.card.supported_interfaces[0]
        assert first.protocol_version == "1.0" and not first.url.endswith("/v03")
        assert "Lisbon" in (await send_text(c, ASK)).text


async def test_an_unsupported_version_is_error_32009(stack) -> None:
    r = rpc(stack["lab10-ext-agent"].url, "SendMessage", message_params(ASK), version="2.0")
    err = r.json()["error"]
    assert err["code"] == -32009 and err["data"][0]["reason"] == "VERSION_NOT_SUPPORTED"


async def test_the_sdk_client_raises_version_not_supported_for_a_future_version(stack) -> None:
    async with await connect(stack["lab10-ext-agent"].url, streaming=False, a2a_version="2.0") as c:
        with pytest.raises(VersionNotSupportedError, match=r"2\.0"):
            await send_text(c, ASK)


async def test_no_version_header_means_0_3_so_a_1_0_method_is_refused(stack) -> None:
    r = rpc(stack["lab10-ext-agent"].url, "SendMessage", message_params(ASK), version=None)
    assert r.json()["error"]["code"] == -32009 and "'0.3'" in r.json()["error"]["message"]


async def test_the_version_query_parameter_is_not_honoured(stack) -> None:
    # The spec allows ?A2A-Version=1.0 for clients that cannot set headers; a2a-sdk 1.2.3 reads
    # the header only (the SDK issue is #1344). Pinned so a future SDK that fixes it shows up.
    r = post_json(
        f"{stack['lab10-ext-agent'].url}/?A2A-Version=1.0",
        {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": message_params(ASK)},
    )
    assert r["error"]["code"] == -32009


async def test_the_v03_mount_accepts_a_legacy_message_send(stack) -> None:
    legacy = {
        "message": {
            "messageId": "m-1",
            "role": "user",
            "kind": "message",
            "parts": [{"kind": "text", "text": ASK}],
        }
    }
    base = stack["lab10-ext-agent"].url
    for path in ("/v03", "/"):  # compat is on for both mounts; the card steers old clients to /v03
        r = post_json(
            f"{base}{path}",
            {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": legacy},
        )["result"]
        assert r["kind"] == "task" and r["status"]["state"] == "completed"
        assert "Lisbon" in r["artifacts"][0]["parts"][0]["text"]

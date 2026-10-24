"""Transport auth, the extended card, AUTH_REQUIRED and REJECTED (spec lab10 §7)."""

from __future__ import annotations

import json
import time

import httpx
import pytest
from a2a.helpers import get_data_parts
from a2a.types import GetExtendedAgentCardRequest, TaskState
from a2a_labs_common.a2a.client import connect, send_text
from a2a_labs_common.auth import OAuthClientCredentialsAuth
from google.protobuf.json_format import MessageToJson
from lab10_secure.clients import (
    StaticCredentials,
    approve,
    card_json,
    client_token,
    device_flow_token,
    message_params,
    rpc,
)
from lab10_secure.data import API_KEYS

PUBLIC = "What's on the public team calendar?"


def url(stack, name: str = "lab10-secure-agent") -> str:
    return stack[name].url


def bearer(**kw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {client_token(**kw)}"}


# ── transport auth ───────────────────────────────────────────────────────────────────────────
async def test_no_credentials_is_a_401_and_the_card_stays_public(stack) -> None:
    base = url(stack)
    r = rpc(base, "SendMessage", message_params(PUBLIC))
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    card = card_json(base)
    assert set(card["securitySchemes"]) == {
        "api_key",
        "oauth2",
        "user_consent",
    }  # discovery works unauthenticated


async def test_bad_credentials_are_a_401_too(stack, monkeypatch) -> None:
    base = url(stack)
    for headers in (
        {"Authorization": "Bearer not.a.jwt"},
        {"Authorization": "Basic abc"},
        {"X-API-Key": "wrong"},
        bearer(audience="someone-else"),  # a real token, minted for another resource server
    ):
        assert rpc(base, "SendMessage", message_params(PUBLIC), headers=headers).status_code == 401


async def test_an_expired_token_is_refused(stack) -> None:
    idp = stack["lab10-mock-idp"].app.state.idp
    token = idp.mint(
        sub="lab10-client",
        client_id="lab10-client",
        scope="agent:invoke",
        audience="a2a-labs-lab10",
    )
    import jwt

    claims = jwt.decode(token, options={"verify_signature": False})
    old = jwt.encode(
        {**claims, "exp": int(time.time()) - 60}, idp._key, "ES256", headers={"kid": idp.kid}
    )
    r = rpc(
        url(stack),
        "SendMessage",
        message_params(PUBLIC),
        headers={"Authorization": f"Bearer {old}"},
    )
    assert r.status_code == 401 and "expired" in r.json()["detail"].lower()


async def test_api_key_and_oauth_both_reach_the_agent(stack) -> None:
    base = url(stack)
    key = next(iter(API_KEYS))
    for headers in ({"X-API-Key": key}, bearer()):
        r = rpc(base, "SendMessage", message_params(PUBLIC), headers=headers)
        assert r.status_code == 200
        assert r.json()["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


async def test_the_auth_interceptor_applies_the_scheme_the_card_declares(stack) -> None:
    # The SDK client reads card.security_schemes and sets the header; we never wrote one.
    async with await connect(url(stack), auth=StaticCredentials({"oauth2": client_token()})) as c:
        res = await send_text(c, PUBLIC)
    assert res.state == TaskState.TASK_STATE_COMPLETED and "Team standup" in res.text
    async with await connect(
        url(stack), auth=StaticCredentials({"api_key": next(iter(API_KEYS))})
    ) as c:
        assert (await send_text(c, PUBLIC)).state == TaskState.TASK_STATE_COMPLETED
    async with await connect(url(stack)) as anonymous:  # no credentials -> the HTTP 401 surfaces
        with pytest.raises(Exception, match=r"401|nauthenticated|Unauthorized"):
            await send_text(anonymous, PUBLIC)


async def test_oauth_client_credentials_httpx_auth_fetches_once_and_caches(stack) -> None:
    auth = OAuthClientCredentialsAuth(
        f"{stack['lab10-mock-idp'].url}/token", "lab10-client", "lab10-secret", "agent:invoke"
    )
    async with httpx.AsyncClient(auth=auth) as http:
        for _ in range(3):
            r = await http.post(
                url(stack),
                json={"jsonrpc": "2.0", "id": 1, "method": "GetExtendedAgentCard"},
                headers={"A2A-Version": "1.0"},
            )
            assert r.status_code == 200
    assert auth.fetches == 1


# ── the extended card ────────────────────────────────────────────────────────────────────────
def extended_skills(base: str, headers: dict[str, str] | None) -> list[str] | int:
    r = rpc(base, "GetExtendedAgentCard", headers=headers)
    if r.status_code != 200:
        return r.status_code
    return [s["id"] for s in r.json()["result"]["skills"]]


async def test_the_extended_card_differs_by_principal(stack) -> None:
    base = url(stack)
    assert extended_skills(base, None) == 401
    basic = extended_skills(base, {"X-API-Key": next(iter(API_KEYS))})
    user = extended_skills(base, bearer())
    admin = extended_skills(
        base, bearer(client_id="lab10-admin-client", scope="agent:invoke agent:admin")
    )
    assert "calendar.free_busy" in basic and "calendar.admin_stats" not in basic
    assert user == basic
    assert "calendar.admin_stats" in admin and "calendar.free_busy" in admin


async def test_the_public_card_hides_what_only_the_extended_card_offers(stack) -> None:
    card = card_json(url(stack))
    ids = [s["id"] for s in card["skills"]]
    assert ids == ["calendar.read_public", "calendar.read_private"]
    assert card["capabilities"]["extendedAgentCard"] is True
    private = next(s for s in card["skills"] if s["id"] == "calendar.read_private")
    assert private["securityRequirements"][0]["schemes"]["user_consent"]["list"] == [
        "calendar:read"
    ]


async def test_the_sdk_client_fetches_the_extended_card(stack) -> None:
    creds = StaticCredentials(
        {"oauth2": client_token(client_id="lab10-admin-client", scope="agent:invoke agent:admin")}
    )
    async with await connect(url(stack), auth=creds) as c:
        card = await c.client.get_extended_agent_card(GetExtendedAgentCardRequest())
    assert "calendar.admin_stats" in [s.id for s in card.skills]


# ── AUTH_REQUIRED: in-task authorization ─────────────────────────────────────────────────────
async def test_auth_required_resumes_the_same_task_and_never_carries_the_user_token(stack) -> None:
    creds = StaticCredentials({"oauth2": client_token()})
    async with await connect(url(stack), auth=creds, streaming=False) as c:
        first = await send_text(c, "Show my private appointments")
        assert first.state == TaskState.TASK_STATE_AUTH_REQUIRED
        challenge = get_data_parts(first.task.status.message.parts)[0]["auth"]
        assert challenge["flow"] == "device_code" and challenge["user_code"]
        assert "device_code" not in challenge  # the secret half stays on the server
        assert challenge["device_code_ref"].startswith("devref_")

        # not approved yet: a reference nobody minted is refused and the task keeps waiting
        retry = await send_text(
            c,
            "ok",
            task_id=first.task_id,
            context_id=first.context_id,
            data={"auth": {"access_token_ref": "tokref_bogus"}},
        )
        assert retry.state == TaskState.TASK_STATE_AUTH_REQUIRED

        ref = approve(challenge["user_code"])  # <- the person, on the IdP's page
        done = await send_text(
            c,
            "approved",
            task_id=first.task_id,
            context_id=first.context_id,
            data={"auth": {"access_token_ref": ref}},
        )
        assert done.state == TaskState.TASK_STATE_COMPLETED and done.task_id == first.task_id
        assert "Dentist" in done.text

        from a2a.types import GetTaskRequest

        task = await c.client.get_task(GetTaskRequest(id=first.task_id, history_length=20))
    dump = MessageToJson(task)
    idp = stack["lab10-mock-idp"].app.state.idp
    leaked = [t for t in idp.issued if t in dump]
    assert leaked == []  # no token of any kind appears in the task history or artifacts
    assert ref in dump  # the REFERENCE does (it is useless once redeemed)
    user_tokens = [
        t
        for t in idp.issued
        if "alice" in json.dumps(__import__("jwt").decode(t, options={"verify_signature": False}))
    ]
    assert user_tokens, "the agent really did obtain a user token from the IdP"


async def test_a_transport_token_that_already_has_calendar_read_skips_the_prompt(stack) -> None:
    # The same agent, two outcomes: consent is only asked for when the caller lacks the scope.
    creds = StaticCredentials({"oauth2": device_flow_token()})
    async with await connect(url(stack), auth=creds, streaming=False) as c:
        res = await send_text(c, "Show my private appointments")
    assert res.state == TaskState.TASK_STATE_COMPLETED and "Dentist" in res.text


# ── REJECTED ─────────────────────────────────────────────────────────────────────────────────
async def test_a_destructive_request_is_rejected_with_the_decision_evidence(stack) -> None:
    async with await connect(
        url(stack), auth=StaticCredentials({"oauth2": client_token()}), streaming=False
    ) as c:
        res = await send_text(c, "Delete all calendars")
    assert res.state == TaskState.TASK_STATE_REJECTED  # not FAILED, not CANCELED
    evidence = get_data_parts(res.task.status.message.parts)[0]
    assert evidence["decision"] == "reject" and evidence["probabilities"]["reject"] >= 0.5


async def test_admin_stats_need_the_admin_scope(stack) -> None:
    plain = StaticCredentials({"oauth2": client_token()})
    admin = StaticCredentials(
        {"oauth2": client_token(client_id="lab10-admin-client", scope="agent:invoke agent:admin")}
    )
    async with await connect(url(stack), auth=plain, streaming=False) as c:
        assert (await send_text(c, "Show admin stats")).state == TaskState.TASK_STATE_REJECTED
    async with await connect(url(stack), auth=admin, streaming=False) as c:
        res = await send_text(c, "Show admin stats")
    assert res.state == TaskState.TASK_STATE_COMPLETED
    assert res.task.artifacts[0].parts[0].data.struct_value["calendars"] == 42


# ── the framework servers use the SAME middleware ────────────────────────────────────────────
@pytest.mark.parametrize("name", ["lab10-adk-secure", "lab10-strands-secure"])
async def test_framework_agents_sit_behind_the_same_bearer_check(stack, name) -> None:
    base = url(stack, name)
    assert rpc(base, "SendMessage", message_params(PUBLIC)).status_code == 401
    # these two accept bearer tokens only: an API key is not a scheme on THEIR card
    key_try = rpc(
        base, "SendMessage", message_params(PUBLIC), headers={"X-API-Key": next(iter(API_KEYS))}
    )
    assert key_try.status_code == 401
    assert card_json(base)["name"]
    async with await connect(
        base, auth=StaticCredentials({"oauth2": client_token()}), streaming=False
    ) as c:
        res = await send_text(c, PUBLIC)
    assert res.state == TaskState.TASK_STATE_COMPLETED


async def test_adk_remote_agent_authenticates_through_a_request_interceptor(stack) -> None:
    from a2a_labs_common.auth import bearer_interceptor
    from google.adk.a2a.agent.config import A2aRemoteAgentConfig
    from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    base = url(stack, "lab10-adk-secure")

    def ask(interceptors) -> RemoteA2aAgent:
        return RemoteA2aAgent(
            name="calendar",
            agent_card=f"{base}/.well-known/agent-card.json",
            config=A2aRemoteAgentConfig(request_interceptors=interceptors),
        )

    async def run(agent) -> str:
        svc = InMemorySessionService()
        runner = Runner(app_name="t", agent=agent, session_service=svc)
        session = await svc.create_session(app_name="t", user_id="u")
        text = ""
        async for ev in runner.run_async(
            user_id="u",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=PUBLIC)]),
        ):
            text += "".join(p.text or "" for p in (ev.content.parts if ev.content else []))
        return text

    ok = await run(ask([bearer_interceptor(client_token)]))
    assert PUBLIC in ok  # the (fake) model answered: the authenticated call got through
    assert await run(ask([])) == ""  # no interceptor: ADK swallows the 401 and yields nothing


async def test_a_framework_style_httpx_client_authenticates_with_oauth_credentials(stack) -> None:
    auth = OAuthClientCredentialsAuth(
        f"{stack['lab10-mock-idp'].url}/token", "lab10-client", "lab10-secret", "agent:invoke"
    )
    http = httpx.AsyncClient(timeout=30, auth=auth)
    async with await connect(
        url(stack, "lab10-strands-secure"), httpx_client=http, streaming=False
    ) as c:
        res = await send_text(c, PUBLIC)
    assert res.state == TaskState.TASK_STATE_COMPLETED and auth.fetches == 1

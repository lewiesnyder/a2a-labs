"""Task ownership: per principal by default, per tenant when the card's interface names one."""

from __future__ import annotations

import pytest
from a2a.types import AgentCard, GetTaskRequest, ListTasksRequest
from a2a.utils.errors import TaskNotFoundError
from a2a_labs_common.a2a.client import connect, connect_with_card, send_text
from lab10_secure.clients import StaticCredentials, client_token

PUBLIC = "What's on the public team calendar?"


async def as_tenant(stack, tenant: str, **token_kw: str):
    base = (await connect(stack["lab10-secure-agent"].url)).card
    card = AgentCard()
    card.CopyFrom(base)
    card.supported_interfaces[0].tenant = tenant  # the client wraps its transport with this
    return await connect_with_card(
        card, auth=StaticCredentials({"oauth2": client_token(**token_kw)}), streaming=False
    )


async def test_one_principal_cannot_read_another_principals_task(stack) -> None:
    url = stack["lab10-secure-agent"].url
    alice = await connect(url, auth=StaticCredentials({"oauth2": client_token()}), streaming=False)
    other = await connect(
        url,
        auth=StaticCredentials(
            {"oauth2": client_token(client_id="lab10-admin-client", scope="agent:invoke")}
        ),
        streaming=False,
    )
    async with alice, other:
        mine = await send_text(alice, PUBLIC)
        assert (await alice.client.get_task(GetTaskRequest(id=mine.task_id))).id == mine.task_id
        with pytest.raises(TaskNotFoundError):
            await other.client.get_task(GetTaskRequest(id=mine.task_id))
        assert not (await other.client.list_tasks(ListTasksRequest())).tasks


async def test_tenants_are_isolated_when_the_owner_includes_the_tenant(stack) -> None:
    acme, globex = await as_tenant(stack, "acme"), await as_tenant(stack, "globex")
    async with acme, globex:
        a = await send_text(acme, PUBLIC)
        g = await send_text(globex, PUBLIC)
        assert [t.id for t in (await acme.client.list_tasks(ListTasksRequest())).tasks] == [
            a.task_id
        ]
        assert [t.id for t in (await globex.client.list_tasks(ListTasksRequest())).tasks] == [
            g.task_id
        ]
        with pytest.raises(TaskNotFoundError):  # the SAME principal, the wrong tenant
            await globex.client.get_task(GetTaskRequest(id=a.task_id))

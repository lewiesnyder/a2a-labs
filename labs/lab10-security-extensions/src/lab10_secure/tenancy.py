"""Who owns a task? The SDK scopes every task to an OWNER, chosen by an ``owner_resolver``.

A2A: ``AgentInterface.tenant`` lets one deployment serve several tenants on one URL. The SDK
carries the tenant to the executor (``RequestContext.tenant``) but isolates nothing by itself:
its task stores key tasks by ``owner_resolver(ServerCallContext)``, which defaults to the
authenticated user's name. Putting the tenant into the owner makes ``GetTask`` / ``ListTasks``
tenant-aware with one line, no custom store.

# CUSTOM: the one-line policy; the hook itself is the SDK's ``OwnerResolver``.
"""

from __future__ import annotations

from a2a.server.context import ServerCallContext


def tenant_owner(context: ServerCallContext) -> str:
    return f"{context.tenant or '-'}/{context.user.user_name}"

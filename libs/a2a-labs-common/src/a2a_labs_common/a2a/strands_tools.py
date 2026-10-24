"""Strands tools that call remote A2A agents (spec lab02 §3.2).

# CUSTOM: the official ``strands.vended_tools.make_a2a_client`` and ``A2AAgent`` import a2a-sdk 0.3
# types and cannot load next to a2a-sdk 1.x (DEV-1, DEV-19). These two factories keep the lesson
# intact: (1) a stateless multi-endpoint tool with an ALLOWLIST, and (2) a stateful single-agent
# tool whose remote context persists. Both use only a2a-sdk calls (through ``RemoteAgent``).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

from strands import tool

from a2a_labs_common.a2a.remote import RemoteAgent

logger = logging.getLogger(__name__)


def make_a2a_client_tool(
    *, name: str, description: str, allowed_endpoints: Mapping[str, str | None]
) -> Any:
    """Stateless tool: ``name(url, message)``. Only URLs in ``allowed_endpoints`` are reachable.

    Each call uses a fresh remote context, so the remote agent keeps no memory between calls.
    The allowlist is a security control: the LLM (or a prompt injection) cannot make the tool
    call an arbitrary URL.
    """
    allowed = {u.rstrip("/") for u in allowed_endpoints}
    agents: dict[str, RemoteAgent] = {}

    @tool(name=name, description=description)
    async def call_specialist(url: str, message: str) -> str:
        """Send a message to a remote specialist agent and return its answer.

        Args:
            url: Base URL of the specialist (must be one of the allowed endpoints).
            message: What to ask the specialist.
        """
        target = url.rstrip("/")
        if target not in allowed:
            logger.warning("blocked a call to a non-allowlisted endpoint: %s", url)
            return f"ERROR: endpoint not allowed: {url}. Allowed: {', '.join(sorted(allowed))}"
        remote = agents.setdefault(target, RemoteAgent(target))
        return (await remote.ask(message, scope=str(uuid.uuid4()))).for_llm()

    return call_specialist


def make_stateful_a2a_tool(*, name: str, description: str, service: str) -> Any:
    """Stateful tool bound to ONE remote agent.

    Remembers the remote ``context_id`` and any task waiting for input, so follow-up calls
    continue the same remote conversation. Create one per controller agent instance.
    """
    remote = RemoteAgent(service)
    scope = str(uuid.uuid4())  # one remote conversation for the life of this tool instance

    @tool(name=name, description=description)
    async def ask_remote(question: str) -> str:
        """Ask the remote agent. If it replies NEEDS_INPUT, relay its question to the user.

        Args:
            question: What to ask, or the user's answer to a pending question.
        """
        return (await remote.ask(question, scope=scope)).for_llm()

    return ask_remote

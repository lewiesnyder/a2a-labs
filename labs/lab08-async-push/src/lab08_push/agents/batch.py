"""The batch summariser: a Strands agent loop over bundled documents, one at a time."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from a2a_labs_common.models import get_strands_model

from lab08_push.agents.data import DOCS, seconds


class BatchAgent:
    """Gives the loop the ``stream_async`` interface ``StrandsA2AExecutor`` drives."""

    def __init__(self, context_id: str = "") -> None:
        from strands import Agent

        self.agent = Agent(
            name="summarizer",
            model=get_strands_model("quick"),
            system_prompt="Summarise the document in one short sentence.",
            callback_handler=None,
        )

    async def stream_async(self, prompt: str, **kw: Any) -> AsyncIterator[dict[str, Any]]:
        for title, text in DOCS.items():
            await asyncio.sleep(seconds("DOC_SECONDS", 2))
            summary = str(await self.agent.invoke_async(f"{title}: {text}")).strip()
            yield {"data": f"- {title}: {summary}\n"}

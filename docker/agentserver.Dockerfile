# Optional sub-lab image (profile lab04-agentserver): a2a-labs:dev plus the LangGraph Agent Server.
# Kept separate so every other service stays free of langgraph-cli/langgraph-api (DEV-30).
FROM a2a-labs:dev
RUN uv pip install --python /app/.venv/bin/python "langgraph-cli[inmem]" "langgraph-api>=0.4.21"

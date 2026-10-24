# Appendix C: Compose Skeleton and Endpoints

Two ready-to-use files ship with this spec:

| File | Destination in repo | Notes |
|---|---|---|
| [`files/compose.yaml`](files/compose.yaml) | `compose.yaml` | Complete service list for all labs (64 services). Validated with `docker compose --profile all config -q` (Compose v5.6). |
| [`files/config/endpoints.yaml`](files/config/endpoints.yaml) | `config/endpoints.yaml` | Generated from the compose file; single source of truth for ports and URLs. |
| [`files/config/models.yaml`](files/config/models.yaml) | `config/models.yaml` | Role → provider/model (see 04 §2). |
| [`files/.env.example`](files/.env.example) | `.env.example` | Same as Appendix B. |

## Conventions inside `compose.yaml`

- `x-lab-service` is the base anchor for every Python service. Each lab service sets:
  - `profiles: [labNN, all]`;
  - `command: ["python", "-m", "<module>"]`;
  - `environment` merged from `x-lab-env`, plus `A2A_LABS_LAB_ID`, `OTEL_SERVICE_NAME` and `PORT`;
  - `ports`, published on the host with the same number;
  - `depends_on` merged from `x-lab-deps`, plus the A2A services it calls (`condition: service_healthy`).
- The healthcheck fetches the agent card. Non-agent services (MCP servers, registry, webhook receiver) set `HEALTH_PATH`. MCP servers MUST also add a `/healthz` route.
- Lab 05 specialists **reuse** the Lab 02 server modules (`lab02_tool.servers.*`) with different service names and ports. That is why `lab05-supervisor-router` depends on `lab02-agent-as-tool`.
- Lab 09 squads list their parent gateway in `AGP_PARENT`. Gateways have `AGP_ROLE` (`root`/`domain`) and `AGP_DOMAIN`.
- `lab10-mock-idp` uses `ghcr.io/navikt/mock-oauth2-server` with `config/lab10/mock-idp.json`. Write that file during M11, configuring the client-credentials client and token claims (`scope`, `aud`).
- If you add a service, update `config/endpoints.yaml` (regenerate it with `scripts/gen_endpoints.py`, which reads `compose.yaml`) and the lab spec's services table.

## Port allocation summary

| Lab | Ports |
|---|---|
| 00 | 10001 (JSON-RPC + REST), 10002 (gRPC), 10003, 10004 |
| 01 | 10101–10104 |
| 02 | 10201–10206 |
| 03 | 10301–10306 (10301 and 10304 are MCP at `/mcp`) |
| 04 | 10401, 10402, 10403 (Agent Server, optional) |
| 05 | 10501–10506 |
| 06 | 10601–10605 |
| 07 | 10701–10704 |
| 08 | 10801–10804 |
| 09 | 10901–10903 (gateways), 10911–10916 (squads) |
| 10 | 11001–11005, 11009 (IdP) |
| shared | 9100 (webhook-receiver), 9200 (embedded receiver in jupyter) |
| infra | 3000, 4317, 4318, 3200, 3100, 9090 (lgtm), 8888 (jupyter), 11434 (ollama), 5001 (inspector), 14317/14318 (collector) |

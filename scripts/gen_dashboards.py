"""Generates the two Grafana dashboards (spec 05 §6) into observability/grafana/dashboards.

Run ``uv run scripts/gen_dashboards.py`` after changing a panel; the JSON is committed so the
stack needs no build step. Metric names are the Prometheus names the OTLP pipeline produces
(checked against a live lgtm: dots become underscores, counters gain ``_total``, histograms
``_bucket/_count/_sum`` with the unit suffix ``_seconds``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent.parent / "observability" / "grafana" / "dashboards"
PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
TEMPO = {"type": "tempo", "uid": "tempo"}
# Series carry `service_name` (e.g. lab05-supervisor...): the lab is its prefix.
LAB = 'service_name=~"$lab_id.*", service_name=~"$service"'


def target(expr: str, legend: str = "", ds: dict[str, str] = PROM, **kw: Any) -> dict[str, Any]:
    return {"datasource": ds, "expr": expr, "legendFormat": legend, "refId": "A", **kw}


def panel(
    i: int,
    title: str,
    kind: str,
    targets: list[dict[str, Any]],
    x: int,
    y: int,
    w: int = 12,
    h: int = 8,
    **kw: Any,
) -> dict[str, Any]:
    for n, t in enumerate(targets):
        t["refId"] = "ABCDEFG"[n]
    ds = targets[0]["datasource"]
    return {
        "id": i,
        "title": title,
        "type": kind,
        "datasource": ds,
        "targets": targets,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        **kw,
    }


def variable(name: str, query: str, regex: str = "", all_value: str = ".*") -> dict[str, Any]:
    return {
        "name": name,
        "label": name,
        "type": "query",
        "datasource": PROM,
        "query": {"query": query, "refId": "v"},
        "regex": regex,
        "includeAll": True,
        "allValue": all_value,
        "multi": False,
        "refresh": 2,
        "current": {"text": "All", "value": "$__all"},
    }


def dash(
    uid: str, title: str, panels: list[dict[str, Any]], variables: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "uid": uid,
        "title": title,
        "tags": ["a2a-labs"],
        "schemaVersion": 39,
        "version": 1,
        "editable": True,
        "time": {"from": "now-1h", "to": "now"},
        "refresh": "10s",
        "templating": {"list": variables},
        "panels": panels,
    }


def overview() -> dict[str, Any]:
    p = [
        panel(
            1,
            "Client requests per method",
            "timeseries",
            [
                target(
                    f"sum by (rpc_method) (rate(a2a_client_requests_total{{{LAB}}}[1m]))",
                    "{{rpc_method}}",
                )
            ],
            0,
            0,
        ),
        panel(
            2,
            "Client errors by A2A error code",
            "timeseries",
            [
                target(
                    f'sum by (a2a_error_code) (rate(a2a_client_requests_total{{{LAB}, outcome="error"}}[1m]))',
                    "{{a2a_error_code}}",
                )
            ],
            12,
            0,
        ),
        panel(
            3,
            "Client duration p50 / p95 per agent",
            "timeseries",
            [
                target(
                    f"histogram_quantile(0.5, sum by (le, a2a_agent_name) (rate(a2a_client_duration_seconds_bucket{{{LAB}}}[5m])))",
                    "p50 {{a2a_agent_name}}",
                ),
                target(
                    f"histogram_quantile(0.95, sum by (le, a2a_agent_name) (rate(a2a_client_duration_seconds_bucket{{{LAB}}}[5m])))",
                    "p95 {{a2a_agent_name}}",
                ),
            ],
            0,
            8,
            fieldConfig={"defaults": {"unit": "s"}, "overrides": []},
        ),
        panel(
            4,
            "Task final states",
            "barchart",
            [
                target(
                    f"sum by (a2a_task_final_state) (a2a_server_task_duration_seconds_count{{{LAB}}})",
                    "{{a2a_task_final_state}}",
                    instant=True,
                )
            ],
            12,
            8,
        ),
        panel(
            5,
            "State transitions (from → to)",
            "table",
            [
                target(
                    f"sum by (from, to) (a2a_server_task_state_transitions_total{{{LAB}}})",
                    "",
                    format="table",
                    instant=True,
                )
            ],
            0,
            16,
        ),
        panel(
            6,
            "Push deliveries by outcome",
            "timeseries",
            [
                target(
                    f"sum by (outcome) (rate(a2a_push_deliveries_total{{{LAB}}}[1m]))",
                    "{{outcome}}",
                )
            ],
            12,
            16,
        ),
        panel(
            7,
            "Decision choices per question (Nimble)",
            "barchart",
            [
                target(
                    f"sum by (question, choice) (decision_choice_total{{{LAB}}})",
                    "{{question}}: {{choice}}",
                    instant=True,
                )
            ],
            0,
            24,
        ),
        panel(
            8,
            "Token usage by model",
            "timeseries",
            [
                target(
                    f"sum by (gen_ai_request_model, gen_ai_token_type) (rate(gen_ai_client_token_usage_sum{{{LAB}}}[5m]))",
                    "{{gen_ai_request_model}} {{gen_ai_token_type}}",
                )
            ],
            12,
            24,
        ),
        panel(
            9,
            "Logs",
            "logs",
            [target('{service_namespace="a2a-labs", service_name=~"$lab_id.*"}', ds=LOKI)],
            0,
            32,
            w=24,
            h=9,
        ),
        panel(
            10,
            "Traces",
            "table",
            [
                {
                    "datasource": TEMPO,
                    "queryType": "traceql",
                    "query": '{ resource.service.namespace = "a2a-labs" }',
                    "limit": 20,
                }
            ],
            0,
            41,
            w=24,
            h=9,
        ),
    ]
    vars_ = [
        variable(
            "lab_id",
            'label_values(up{job=~"a2a-labs/.*"}, job)',
            regex="/a2a-labs\\/(lab\\d\\d)-.*/",
            all_value="lab",
        ),
        variable(
            "service",
            'label_values(a2a_client_requests_total{service_name=~"$lab_id.*"}, service_name)',
        ),
    ]
    return dash("a2a-labs-overview", "A2A Labs Overview", p, vars_)


def lifecycle() -> dict[str, Any]:
    p = [
        panel(
            1,
            "Trace search for the task",
            "table",
            [
                {
                    "datasource": TEMPO,
                    "queryType": "traceql",
                    "query": '{ span.a2a.task.id = "$task_id" }',
                    "limit": 20,
                }
            ],
            0,
            0,
            w=24,
            h=9,
        ),
        panel(
            2,
            "Logs mentioning the task",
            "logs",
            [target('{service_namespace="a2a-labs"} |= "$task_id"', ds=LOKI)],
            0,
            9,
            w=24,
            h=10,
        ),
        panel(
            3,
            "State-change events",
            "table",
            [
                {
                    "datasource": TEMPO,
                    "queryType": "traceql",
                    "query": '{ span.a2a.task.id = "$task_id" } | select(event.name, event.from, event.to)',
                    "limit": 50,
                }
            ],
            0,
            19,
            w=24,
            h=9,
        ),
    ]
    tv = {
        "name": "task_id",
        "label": "task_id",
        "type": "textbox",
        "query": "",
        "current": {"text": "", "value": ""},
    }
    return dash("a2a-task-lifecycle", "A2A Task Lifecycle", p, [tv])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, d in (
        ("a2a-labs-overview.json", overview()),
        ("a2a-task-lifecycle.json", lifecycle()),
    ):
        (OUT / name).write_text(json.dumps(d, indent=2) + "\n")
        print("wrote", OUT / name)


if __name__ == "__main__":
    main()

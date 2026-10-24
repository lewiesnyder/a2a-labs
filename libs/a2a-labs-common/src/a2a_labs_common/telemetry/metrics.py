"""Metric instruments (spec 05 §5). Created lazily so they bind to the provider set by init."""

from __future__ import annotations

from functools import lru_cache

from opentelemetry import metrics
from opentelemetry.metrics import Counter, Histogram


@lru_cache
def _m() -> metrics.Meter:
    return metrics.get_meter("a2a_labs_common")


def _counter(name: str, unit: str, desc: str) -> Counter:
    return _m().create_counter(name, unit=unit, description=desc)


def _hist(name: str, desc: str) -> Histogram:
    return _m().create_histogram(name, unit="s", description=desc)


class Instruments:
    def __init__(self) -> None:
        self.client_requests = _counter("a2a.client.requests", "{request}", "A2A client calls")
        self.client_duration = _hist("a2a.client.duration", "A2A client call duration")
        self.client_ttfe = _hist("a2a.client.ttfe", "Time to first streamed event")
        self.tasks_started = _counter("a2a.server.tasks.started", "{task}", "Tasks started")
        self.task_duration = _hist("a2a.server.task.duration", "Task execution duration")
        self.transitions = _counter(
            "a2a.server.task.state_transitions", "{transition}", "Task state transitions"
        )
        self.artifact_chunks = _counter(
            "a2a.server.artifact.chunks", "{chunk}", "Artifact chunks emitted"
        )
        self.push_deliveries = _counter("a2a.push.deliveries", "{delivery}", "Push deliveries")
        self.push_received = _counter("a2a.push.received", "{notification}", "Push received")
        self.decision_duration = _hist("decision.duration", "Decision model latency")
        self.decision_choice = _counter("decision.choice", "{decision}", "Decisions made")


@lru_cache
def instruments() -> Instruments:
    return Instruments()

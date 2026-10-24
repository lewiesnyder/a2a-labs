"""Task-state groupings (spec 03 §5, 01 §5.2)."""

from __future__ import annotations

from a2a.types import TaskState

TERMINAL = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    }
)
INTERRUPTED = frozenset({TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED})


def is_terminal(state: TaskState) -> bool:
    return state in TERMINAL


def is_interrupted(state: TaskState) -> bool:
    return state in INTERRUPTED


def state_name(state: TaskState) -> str:
    """``TASK_STATE_WORKING`` -> ``WORKING``."""
    return str(TaskState.Name(state)).removeprefix("TASK_STATE_")

"""Single source for the operation crosswalk table (spec 01 §2, display.crosswalk_table)."""

from __future__ import annotations

from typing import NamedTuple


class Method(NamedTuple):
    requested: bool  # one of the seven operations named in the brief
    operation: str
    legacy: str
    v03: str
    v10_jsonrpc: str
    v10_rest: str
    sdk_client: str


METHOD_CROSSWALK: tuple[Method, ...] = (
    Method(
        True,
        "Send message",
        "tasks/send",
        "message/send",
        "SendMessage",
        "POST /message:send",
        "send_message (streaming=False)",
    ),
    Method(
        True,
        "Send streaming",
        "tasks/sendSubscribe",
        "message/stream",
        "SendStreamingMessage",
        "POST /message:stream",
        "send_message (streaming=True)",
    ),
    Method(True, "Get task", "tasks/get", "tasks/get", "GetTask", "GET /tasks/{id}", "get_task"),
    Method(False, "List tasks", "", "", "ListTasks", "GET /tasks", "list_tasks"),
    Method(
        True,
        "Cancel task",
        "tasks/cancel",
        "tasks/cancel",
        "CancelTask",
        "POST /tasks/{id}:cancel",
        "cancel_task",
    ),
    Method(
        True,
        "Re-attach stream",
        "tasks/resubscribe",
        "tasks/resubscribe",
        "SubscribeToTask",
        "POST /tasks/{id}:subscribe",
        "subscribe",
    ),
    Method(
        True,
        "Set push config",
        "tasks/pushNotification/set",
        "tasks/pushNotificationConfig/set",
        "CreateTaskPushNotificationConfig",
        "POST /tasks/{id}/pushNotificationConfigs",
        "create_task_push_notification_config",
    ),
    Method(
        True,
        "Get push config",
        "tasks/pushNotification/get",
        "tasks/pushNotificationConfig/get",
        "GetTaskPushNotificationConfig",
        "GET /tasks/{id}/pushNotificationConfigs/{configId}",
        "get_task_push_notification_config",
    ),
    Method(
        False,
        "List push configs",
        "",
        "tasks/pushNotificationConfig/list",
        "ListTaskPushNotificationConfigs",
        "GET /tasks/{id}/pushNotificationConfigs",
        "list_task_push_notification_configs",
    ),
    Method(
        False,
        "Delete push config",
        "",
        "tasks/pushNotificationConfig/delete",
        "DeleteTaskPushNotificationConfig",
        "DELETE /tasks/{id}/pushNotificationConfigs/{configId}",
        "delete_task_push_notification_config",
    ),
    Method(
        False,
        "Extended card",
        "",
        "agent/getAuthenticatedExtendedCard",
        "GetExtendedAgentCard",
        "GET extended card endpoint",
        "get_extended_agent_card",
    ),
)

LEGACY_NAMES = tuple(m.legacy for m in METHOD_CROSSWALK if m.legacy)

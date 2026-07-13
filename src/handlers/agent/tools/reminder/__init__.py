"""Reminder function-call tool registration."""

from handlers.agent.tools.reminder.tool import ManageReminderTool


def register_tools(registry, *, context=None, **_kwargs) -> None:
    """Register the reminder tool through the project's existing loader."""
    registry.register(ManageReminderTool(context=context))


__all__ = ["ManageReminderTool", "register_tools"]

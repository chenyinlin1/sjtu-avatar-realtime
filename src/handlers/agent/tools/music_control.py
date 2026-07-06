"""Music player control tool.

This tool intentionally does not search or resolve songs. It only normalizes a
player control request so callers can dispatch it to a frontend music player.
"""

from __future__ import annotations

from typing import Any, Dict

from handlers.agent.tools.base_tool import BaseTool, ToolResult


class MusicControlTool(BaseTool):
    @property
    def name(self) -> str:
        return "music_control"

    @property
    def category(self) -> str:
        return "music"

    @property
    def description(self) -> str:
        return (
            "音乐播放器控制工具。当用户说暂停、继续、下一首、音量小一点、"
            "音量大一点、静音或取消静音时使用。工具只返回结构化控制动作，"
            "实际播放由前端播放器执行。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["stop", "pause", "resume", "next", "volume", "mute", "unmute"],
                    "description": "播放器控制动作。",
                },
                "delta": {
                    "type": "number",
                    "description": "音量调整幅度。action=volume 时使用，负数调小，正数调大。",
                },
            },
            "required": ["action"],
        }

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        action = str(args.get("action", "")).strip().lower()
        if action not in {"stop", "pause", "resume", "next", "volume", "mute", "unmute"}:
            return ToolResult(success=False, error=f"Unsupported music control action: {action}")
        data = {
            "type": "music.control",
            "action": action,
            "hints": ["停止", "暂停", "继续", "下一首", "音量小一点"],
        }
        if action == "volume":
            try:
                data["delta"] = float(args.get("delta", 0))
            except (TypeError, ValueError):
                data["delta"] = 0
        return ToolResult(success=True, data=data)


def register_tools(registry, **_kwargs) -> None:
    registry.register(MusicControlTool())

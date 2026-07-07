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
            "音乐播放器控制工具。当用户想控制正在播放的音乐时使用，包括暂停、继续、停止、退出、"
            "关闭、下一首、调节音量、静音或取消静音。用户语音转写文本可能不准确，"
            "只要语义相近也应使用本工具，例如“停止播放、停止音乐、停下音乐、停一下音乐、"
            "关掉音乐、关闭音乐、别放了、不听了、退出播放、停歌”，以及 ASR 可能误写成的"
            "“停子播放、停子音乐、关止音乐”等。停止/关闭/退出/不听了等语义应传 action=stop；"
            "暂停语义传 action=pause；继续/恢复/播放语义传 action=resume。"
            "工具只返回结构化控制动作，实际播放由前端播放器执行。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["pause", "resume", "stop", "next", "volume", "mute", "unmute"],
                    "description": (
                        "播放器控制动作。停止、关闭、退出、不听了、别放了、停歌、停子播放、"
                        "停子音乐、关止音乐等语义使用 stop；暂停使用 pause；继续、恢复、播放使用 resume。"
                    ),
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
        if action not in {"pause", "resume", "stop", "next", "volume", "mute", "unmute"}:
            return ToolResult(success=False, error=f"Unsupported music control action: {action}")
        data = {
            "type": "music.control",
            "action": action,
            "hints": ["暂停", "继续", "下一首", "音量小一点"],
        }
        if action == "volume":
            try:
                data["delta"] = float(args.get("delta", 0))
            except (TypeError, ValueError):
                data["delta"] = 0
        return ToolResult(success=True, data=data)


def register_tools(registry, **_kwargs) -> None:
    registry.register(MusicControlTool())

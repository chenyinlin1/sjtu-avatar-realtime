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
            "音乐播放器控制工具。当用户表达控制当前音乐/音频时使用，包括暂停、继续/恢复、重播/从头播放、停止/关闭/退出播放、上一首、下一首、音量调大/调小、静音、取消静音。"
            "口语、四川话或 ASR 近似表达也应识别，例如“停一哈”“莫放了”“不听了”“不想听这首”“接倒放”“声音小点”。"
            "其中“不听了”“不想听这首”“莫放了”“别放了”“关了”应返回 action=stop；“暂停”“停一哈”应返回 action=pause；“接倒放”“继续放”应返回 action=resume；“重播”“重放”“重新播放”“再放一遍”应返回 action=replay。"
            "若用户是在点歌、搜索音乐、推荐歌曲、询问歌词或闲聊，不应调用该工具。"
            "工具只返回结构化控制动作，实际播放控制由前端播放器执行。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["stop", "pause", "resume", "replay", "restart", "next", "volume", "mute", "unmute"],
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
        if action == "restart":
            action = "replay"
        if action not in {"stop", "pause", "resume", "replay", "next", "volume", "mute", "unmute"}:
            return ToolResult(success=False, error=f"Unsupported music control action: {action}")
        data = {
            "type": "music.control",
            "action": action,
            "hints": ["停止", "暂停", "继续", "重播", "下一首", "音量小一点"],
        }
        if action == "volume":
            try:
                data["delta"] = float(args.get("delta", 0))
            except (TypeError, ValueError):
                data["delta"] = 0
        return ToolResult(success=True, data=data)


def register_tools(registry, **_kwargs) -> None:
    registry.register(MusicControlTool())

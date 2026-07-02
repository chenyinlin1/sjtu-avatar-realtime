"""Music request tool inspired by astrbot_plugin_music.

The AstrBot plugin exposes an LLM tool that searches a song by name and sends it
through a configured player. In this project we keep the same low-coupling shape:
the tool resolves a song request to structured metadata and a playable URL, while
the caller decides how to present or play it.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from handlers.agent.tools.base_tool import BaseTool, ToolResult


DEFAULT_NETEASE_NODEJS_BASE_URLS = [
    "https://163api.qijieya.cn",
    "https://wyy.xhily.com",
]


@dataclass
class SongCandidate:
    song_id: int
    title: str
    artists: List[str]
    album: str = ""
    duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.song_id,
            "title": self.title,
            "artists": self.artists,
            "artist": " / ".join(self.artists),
            "album": self.album,
            "duration_ms": self.duration_ms,
        }


class MusicRequestTool(BaseTool):
    """Search a song and return a playable text-link result."""

    def __init__(
        self,
        *,
        base_urls: Optional[List[str]] = None,
        timeout: float = 10.0,
        default_limit: int = 5,
    ):
        self._base_urls = base_urls or _load_base_urls()
        self._timeout = timeout
        self._default_limit = default_limit

    @property
    def name(self) -> str:
        return "music_request"

    @property
    def category(self) -> str:
        return "music"

    @property
    def requires(self) -> list[str]:
        return ["network"]

    @property
    def timeout(self) -> float:
        return self._timeout

    @property
    def description(self) -> str:
        return (
            "点歌工具。当用户说想听某首歌、点歌、播放音乐、找歌曲链接时使用。"
            "根据歌曲名或歌手关键词搜索音乐，并返回可播放链接、歌曲信息和候选列表。"
            "当前默认使用网易云 NodeJS API 公共服务，发送方式为文本链接。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "song_name": {
                    "type": "string",
                    "description": "歌曲名称或包含歌手的搜索关键词，如“晴天 周杰伦”。",
                },
                "platform": {
                    "type": "string",
                    "enum": ["netease_nodejs"],
                    "description": "音乐平台。当前实现 netease_nodejs，兼容 astrbot_plugin_music 的 nj/网易云 NodeJS 思路。",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "搜索候选数量，默认 5。",
                },
                "select_index": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "选择第几个候选结果，默认 1。",
                },
            },
            "required": ["song_name"],
        }

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        song_name = str(args.get("song_name", "")).strip()
        if not song_name:
            return ToolResult(success=False, error="song_name is required")

        platform = args.get("platform") or "netease_nodejs"
        if platform != "netease_nodejs":
            return ToolResult(success=False, error=f"Unsupported music platform: {platform}")

        limit = min(max(_coerce_int(args.get("limit"), self._default_limit), 1), 10)
        select_index = min(max(_coerce_int(args.get("select_index"), 1), 1), limit)

        errors = []
        for base_url in self._base_urls:
            try:
                candidates = self._search_netease(base_url, song_name, limit)
                if not candidates:
                    errors.append(f"{base_url}: no results")
                    continue
                selected = candidates[min(select_index - 1, len(candidates) - 1)]
                play_url = self._get_netease_play_url(base_url, selected.song_id)
                return ToolResult(
                    success=True,
                    data={
                        "status": "resolved",
                        "platform": "netease_nodejs",
                        "source": base_url,
                        "query": song_name,
                        "selected": selected.to_dict(),
                        "play_url": play_url,
                        "send_mode": "text",
                        "message": _build_message(selected, play_url),
                        "candidates": [c.to_dict() for c in candidates],
                    },
                )
            except Exception as e:
                errors.append(f"{base_url}: {type(e).__name__}: {e}")

        return ToolResult(
            success=False,
            error="Music request failed; " + " | ".join(errors),
        )

    def _search_netease(
        self,
        base_url: str,
        keyword: str,
        limit: int,
    ) -> List[SongCandidate]:
        payload = _get_json(
            base_url,
            "/search",
            {"keywords": keyword, "limit": limit},
            timeout=self._timeout,
        )
        songs = ((payload.get("result") or {}).get("songs") or [])[:limit]
        return [_parse_song(song) for song in songs if song.get("id")]

    def _get_netease_play_url(self, base_url: str, song_id: int) -> str:
        for path, params in (
            ("/song/url/v1", {"id": song_id, "level": "standard"}),
            ("/song/url", {"id": song_id}),
        ):
            payload = _get_json(base_url, path, params, timeout=self._timeout)
            items = payload.get("data") or []
            if items and items[0].get("url"):
                return items[0]["url"]
        return ""


def _load_base_urls() -> List[str]:
    raw = os.getenv("MUSIC_NODEJS_BASE_URLS") or os.getenv("MUSIC_NODEJS_BASE_URL")
    if not raw:
        return DEFAULT_NETEASE_NODEJS_BASE_URLS
    urls = [part.strip().rstrip("/") for part in raw.split(",") if part.strip()]
    return urls or DEFAULT_NETEASE_NODEJS_BASE_URLS


def _get_json(
    base_url: str,
    path: str,
    params: Dict[str, Any],
    *,
    timeout: float,
) -> Dict[str, Any]:
    url = base_url.rstrip("/") + path + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "OpenAvatarChat/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except TimeoutError as e:
        raise RuntimeError("request timed out") from e
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(str(e)) from e
    return json.loads(body)


def _parse_song(song: Dict[str, Any]) -> SongCandidate:
    artists = song.get("artists") or song.get("ar") or []
    album = song.get("album") or song.get("al") or {}
    return SongCandidate(
        song_id=int(song.get("id")),
        title=song.get("name") or "",
        artists=[a.get("name", "") for a in artists if a.get("name")],
        album=album.get("name", "") if isinstance(album, dict) else "",
        duration_ms=_coerce_int(song.get("duration") or song.get("dt"), 0),
    )


def _build_message(song: SongCandidate, play_url: str) -> str:
    artist = " / ".join(song.artists) or "未知歌手"
    if play_url:
        return f"已为你找到《{song.title}》 - {artist}，播放链接：{play_url}"
    return f"已为你找到《{song.title}》 - {artist}，但暂时没有拿到可播放链接。"


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def register_tools(registry, **_kwargs) -> None:
    registry.register(MusicRequestTool())

"""Web search tool backed by Bocha Web Search API."""

from __future__ import annotations

import os
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List

from handlers.agent.tools.base_tool import BaseTool, ToolResult


class BochaWebSearchTool(BaseTool):
    """Search the web with Bocha's LLM-oriented search API."""

    DEFAULT_ENDPOINT = "https://api.bochaai.com/v1/web-search"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
        default_count: int = 8,
        timeout: float = 15.0,
    ):
        self._api_key = api_key or os.getenv("BOCHA_API_KEY") or os.getenv("BOCHAAI_API_KEY")
        self._endpoint = endpoint or os.getenv("BOCHA_WEB_SEARCH_URL") or self.DEFAULT_ENDPOINT
        self._default_count = default_count
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def category(self) -> str:
        return "web"

    @property
    def requires(self) -> list[str]:
        return ["network"]

    @property
    def timeout(self) -> float:
        return self._timeout

    @property
    def description(self) -> str:
        return (
            "搜索互联网网页信息。当用户询问最新消息、实时资料、事实核验、"
            "天气、赛事赛果、赛程、新闻、价格、政策、产品信息，"
            "或任何需要联网查询的问题时使用。返回网页标题、链接、摘要、站点和发布时间。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索查询。用清晰、具体的自然语言描述要查找的信息。",
                },
                "freshness": {
                    "type": "string",
                    "enum": ["noLimit", "oneDay", "oneWeek", "oneMonth", "oneYear"],
                    "description": "时间范围。默认 noLimit；查新闻或最新信息可用 oneDay/oneWeek/oneMonth。",
                },
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "返回结果数量，1 到 50。默认 8。",
                },
                "summary": {
                    "type": "boolean",
                    "description": "是否请求博查返回更完整摘要。默认 true。",
                },
            },
            "required": ["query"],
        }

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        if not self._api_key:
            return ToolResult(
                success=False,
                error="BOCHA_API_KEY is not configured",
            )

        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult(success=False, error="query is required")

        count = _coerce_int(args.get("count"), self._default_count)
        count = min(max(count, 1), 50)
        freshness = args.get("freshness") or "noLimit"
        if freshness not in {"noLimit", "oneDay", "oneWeek", "oneMonth", "oneYear"}:
            freshness = "noLimit"
        summary = args.get("summary")
        if summary is None:
            summary = True

        payload = {
            "query": query,
            "freshness": freshness,
            "summary": bool(summary),
            "count": count,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw_body = response.read().decode("utf-8", errors="replace")
            raw = json.loads(raw_body)
        except TimeoutError:
            return ToolResult(success=False, error="Bocha web search timed out")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            return ToolResult(
                success=False,
                error=f"Bocha web search HTTP {e.code}: {body}",
            )
        except urllib.error.URLError as e:
            return ToolResult(success=False, error=f"Bocha web search request failed: {e}")
        except json.JSONDecodeError as e:
            return ToolResult(success=False, error=f"Bocha web search returned invalid JSON: {e}")

        results = _extract_web_results(raw)
        return ToolResult(
            success=True,
            data={
                "query": query,
                "freshness": freshness,
                "count": len(results),
                "results": results,
            },
        )


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _extract_web_results(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    web_pages = ((raw.get("data") or {}).get("webPages") or {})
    values = web_pages.get("value") or []
    results: List[Dict[str, Any]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        content = item.get("summary") or item.get("snippet") or ""
        result = {
            "title": item.get("name") or item.get("title") or "",
            "url": item.get("url") or "",
            "content": content,
            "snippet": item.get("snippet") or "",
            "summary": item.get("summary") or "",
            "site_name": item.get("siteName") or "",
            "site_icon": item.get("siteIcon") or "",
            "date_published": item.get("datePublished") or item.get("dateLastCrawled") or "",
        }
        if result["title"] or result["url"] or result["content"]:
            results.append(result)
    return results


def register_tools(registry, **kwargs) -> None:
    config = kwargs.get("config")
    context = kwargs.get("context")
    source = context or config
    default_count = getattr(source, "web_search_result_limit", None) or 8
    timeout = getattr(source, "web_search_timeout", None) or 15.0
    registry.register(BochaWebSearchTool(
        api_key=getattr(source, "bocha_api_key", None),
        endpoint=getattr(source, "bocha_endpoint", None),
        default_count=default_count,
        timeout=timeout,
    ))

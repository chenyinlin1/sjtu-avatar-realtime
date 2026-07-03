import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def search_bocha(
    query: str,
    api_key: str,
    *,
    endpoint: str = "https://api.bochaai.com/v1/web-search",
    timeout: float = 3.0,
    result_limit: int = 5,
) -> List[SearchResult]:
    payload = {
        "query": query,
        "summary": True,
        "count": result_limit,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Bocha search HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Bocha search request failed: {exc}") from exc

    data = json.loads(response_body)
    return _parse_bocha_results(data, result_limit)


def format_search_results(results: List[SearchResult]) -> str:
    if not results:
        return ""
    lines = []
    for index, result in enumerate(results, start=1):
        lines.append(
            f"{index}. {result.title}\n"
            f"摘要: {result.snippet}\n"
            f"来源: {result.url}"
        )
    return "\n\n".join(lines)


def _parse_bocha_results(data: Dict[str, Any], result_limit: int) -> List[SearchResult]:
    web_pages = data.get("data", {}).get("webPages", {}).get("value", [])
    if not isinstance(web_pages, list):
        return []

    results: List[SearchResult] = []
    for item in web_pages[:result_limit]:
        if not isinstance(item, dict):
            continue
        title = _first_text(item, ["name", "title"])
        url = _first_text(item, ["url", "link"])
        snippet = _first_text(item, ["summary", "snippet", "description"])
        if not title and not snippet:
            continue
        results.append(SearchResult(title=title or url, url=url, snippet=snippet))
    return results


def _first_text(item: Dict[str, Any], keys: List[str]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""

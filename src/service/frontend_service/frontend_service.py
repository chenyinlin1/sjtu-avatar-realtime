"""
Shared frontend registration utilities.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import gradio
from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from loguru import logger

from src.engine_utils.directory_info import DirectoryInfo

InitConfigSource = Union[Dict[str, Any], Callable[[], Dict[str, Any]]]


def _add_no_cache_headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


class NoCacheHTMLStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        if path.endswith(".html"):
            _add_no_cache_headers(response)
        return response


def _frontend_entry_version(frontend_path: Path) -> str:
    index_path = frontend_path / "index.html"
    try:
        return str(index_path.stat().st_mtime_ns)
    except FileNotFoundError:
        return "missing"


def _with_version_query(url: str, version: str) -> str:
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "v"]
    query.append(("v", version))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _no_cache_redirect(url: str) -> RedirectResponse:
    return _add_no_cache_headers(RedirectResponse(url=url))


@dataclass
class FrontendRegistrationOptions:
    """
    Options that control how the shared frontend is mounted.
    """

    mount_path: str = "/ui"
    root_route: str = "/"
    redirect_target: str = "/ui/index.html"
    init_config_route: str = "/openavatarchat/initconfig"
    frontend_dist_relative_path: str = "service/frontend_service/frontend/dist"
    gradio_placeholder_html: str = (
        """
        <h1 id="openavatarchat">
           The Gradio page is no longer available. Please use the openavatarchat-webui submodule instead.
        </h1>
        """
    )


def _resolve_frontend_path(options: FrontendRegistrationOptions) -> Path:
    return Path(DirectoryInfo.get_src_dir()) / options.frontend_dist_relative_path


def _materialize_init_config(init_config: InitConfigSource) -> Dict[str, Any]:
    if callable(init_config):
        config = init_config()
    else:
        config = copy.deepcopy(init_config)

    if not isinstance(config, dict):
        raise ValueError("init_config must resolve to a dictionary.")

    return config


def register_frontend(
    app: FastAPI,
    ui: gradio.blocks.Block,
    parent_block: Optional[gradio.blocks.Block],
    init_config: InitConfigSource,
    options: Optional[FrontendRegistrationOptions] = None,
):
    """
    Register the shared Web UI, init config endpoint, and placeholder Gradio notice.
    """

    opts = options or FrontendRegistrationOptions()
    frontend_path = _resolve_frontend_path(opts)

    @app.get(opts.init_config_route)
    async def init_config_endpoint():
        config = _materialize_init_config(init_config)
        return JSONResponse(status_code=200, content=config)

    if frontend_path.exists():
        logger.info(f"Serving frontend from {frontend_path}")
        app.mount(opts.mount_path, NoCacheHTMLStaticFiles(directory=frontend_path), name="static")

        async def frontend_root_redirect():
            versioned_target = _with_version_query(
                opts.redirect_target,
                _frontend_entry_version(frontend_path),
            )
            return _no_cache_redirect(versioned_target)

        app.add_api_route(
            opts.root_route,
            frontend_root_redirect,
            methods=["GET"],
            include_in_schema=False,
        )
    else:
        logger.warning(f"Frontend directory {frontend_path} does not exist")

        async def gradio_root_redirect():
            return _no_cache_redirect("/gradio")

        app.add_api_route(
            opts.root_route,
            gradio_root_redirect,
            methods=["GET"],
            include_in_schema=False,
        )

    active_parent = parent_block or ui
    with ui:
        with active_parent:
            gradio.components.HTML(opts.gradio_placeholder_html, visible=True)

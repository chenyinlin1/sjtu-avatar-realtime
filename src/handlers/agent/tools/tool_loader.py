"""Config-driven tool module loader.

Tool modules can expose either:
  - register_tools(registry, config=..., context=...)
  - get_tools(config=..., context=...) -> Iterable[BaseTool]

This keeps ChatAgent independent from concrete tool implementations.
"""

from __future__ import annotations

import importlib
from typing import Any, Iterable

from loguru import logger

from handlers.agent.tools.base_tool import BaseTool


def load_tool_modules(
    registry,
    module_names: Iterable[str],
    *,
    config: Any = None,
    context: Any = None,
) -> None:
    for module_name in module_names:
        if not module_name:
            continue
        try:
            module = importlib.import_module(module_name)
        except Exception as e:
            logger.warning(f"[ToolLoader] Failed to import {module_name}: {e}")
            continue

        register = getattr(module, "register_tools", None)
        if callable(register):
            try:
                register(registry, config=config, context=context)
                logger.info(f"[ToolLoader] Registered tools from {module_name}")
            except TypeError:
                register(registry)
                logger.info(f"[ToolLoader] Registered tools from {module_name}")
            except Exception as e:
                logger.warning(f"[ToolLoader] {module_name}.register_tools failed: {e}")
            continue

        get_tools = getattr(module, "get_tools", None)
        if callable(get_tools):
            try:
                tools = get_tools(config=config, context=context)
            except TypeError:
                tools = get_tools()
            _register_iterable(registry, tools, module_name)
            continue

        logger.warning(
            f"[ToolLoader] {module_name} has no register_tools() or get_tools()"
        )


def _register_iterable(registry, tools: Iterable[BaseTool] | None, module_name: str) -> None:
    if tools is None:
        return
    for tool in tools:
        if isinstance(tool, BaseTool):
            registry.register(tool)
        else:
            logger.warning(f"[ToolLoader] Ignoring non-tool from {module_name}: {tool!r}")

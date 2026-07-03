"""
BaseTool — abstract base class for all tools callable by the agent.

Each tool provides:
  - name, description: for LLM tool schema
  - parameters: JSON Schema dict describing the tool's input
  - execute(args) -> ToolResult: synchronous execution
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import json


@dataclass
class ToolResult:
    """Standardized result returned by tool execution."""
    success: bool = True
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_content_str(self) -> str:
        """Serialize to a string suitable for the tool_result message content."""
        if not self.success:
            return json.dumps({"error": self.error or "unknown error"}, ensure_ascii=False)
        return json.dumps(self.data, ensure_ascii=False, default=str)


class BaseTool(ABC):
    """Abstract base class for agent tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool name (used as function name in tool_call)."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description shown to LLM."""
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict:
        """JSON Schema dict describing input parameters.

        Example:
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "search query"}
                },
                "required": ["query"]
            }
        """
        ...

    @abstractmethod
    def execute(self, args: Dict[str, Any]) -> ToolResult:
        """Execute the tool with the given arguments.

        Must return a ToolResult. For async tools, execute() should submit
        the task and immediately return {"status": "submitted", ...}.
        """
        ...

    @property
    def category(self) -> str:
        """Optional category for filtering, logging, and UI grouping."""
        return "general"

    @property
    def requires(self) -> list[str]:
        """Optional capability requirements, such as network or oc_bridge."""
        return []

    @property
    def timeout(self) -> float:
        """Recommended execution timeout in seconds."""
        return 15.0

    @property
    def dangerous(self) -> bool:
        """Whether this tool can perform sensitive or destructive actions."""
        return False

    @property
    def async_supported(self) -> bool:
        """Whether this tool may submit work asynchronously."""
        return False

    def get_openai_schema(self, strict: bool = False) -> dict:
        """Return OpenAI/DeepSeek-compatible function calling tool schema."""
        parameters = deepcopy(self.parameters)
        function_schema = {
            "name": self.name,
            "description": self.description,
            "parameters": _normalize_schema(parameters, strict=strict),
        }
        if strict:
            function_schema["strict"] = True
        return {
            "type": "function",
            "function": function_schema,
        }


def _normalize_schema(schema: dict, strict: bool = False) -> dict:
    """Normalize a JSON schema for provider-specific strict tool calling.

    DeepSeek strict mode requires every object to set additionalProperties=false
    and list all declared properties as required.
    """
    if not strict or not isinstance(schema, dict):
        return schema

    schema_type = schema.get("type")
    properties = schema.get("properties")
    if schema_type == "object" or isinstance(properties, dict):
        schema["type"] = "object"
        schema["additionalProperties"] = False
        schema["required"] = list(properties.keys()) if properties else []
        if properties:
            for child in properties.values():
                _normalize_schema(child, strict=True)

    items = schema.get("items")
    if isinstance(items, dict):
        _normalize_schema(items, strict=True)

    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        for child in any_of:
            if isinstance(child, dict):
                _normalize_schema(child, strict=True)

    defs = schema.get("$def") or schema.get("$defs")
    if isinstance(defs, dict):
        for child in defs.values():
            if isinstance(child, dict):
                _normalize_schema(child, strict=True)

    return schema

"""
Tool Registry

Registry of the tools available to the runner.
"""

from typing import Any, Dict, List

from loguru import logger

from runner.memory import default_index_path

from .base import Tool
from .browser import BrowserTool
from .document_reader import DocumentReaderTool
from .explorer import ExplorerTool
from .filesystem import FilesystemTool
from .memory_search import MemorySearchTool
from .shell import ShellTool


class ToolRegistry:
    """Registry di tools"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.tools: Dict[str, Tool] = {}

        # Registra i tools abilitati
        enabled_tools = config.get("tools", {}).get("enabled", [])
        self._register_builtin_tools(enabled_tools)

    def _register_builtin_tools(self, enabled: List[str]):
        """Registra i tools built-in"""
        if "filesystem" in enabled:
            self.register(FilesystemTool(self.config))

        if "shell" in enabled:
            self.register(ShellTool(self.config))

        if "browser" in enabled:
            self.register(BrowserTool(self.config))

        if "document_reader" in enabled:
            self.register(DocumentReaderTool(self.config))

        if "explorer" in enabled:
            self.register(ExplorerTool(self.config))

        # Offered once a memory has been indexed on this machine, or when named.
        # The policy still decides whether it runs: tools are default-deny.
        if "memory_search" in enabled or default_index_path().exists():
            self.register(MemorySearchTool(self.config))

    def register(self, tool: Tool):
        """Register a tool."""
        self.tools[tool.name] = tool
        logger.debug(f"Registered tool: {tool.name}")

    def get_tool(self, name: str) -> Tool:
        """Look a tool up by name."""
        if name not in self.tools:
            raise ValueError(f"Tool not found: {name}")
        return self.tools[name]

    def list_tools(self) -> List[str]:
        """List every available tool."""
        return list(self.tools.keys())

    def get_tool_schema(self, name: str) -> Dict[str, Any]:
        """The schema advertised to the model for one tool."""
        tool = self.get_tool(name)
        return {"name": tool.name, "description": tool.description, "parameters": tool.parameters}

    def get_all_schemas(self) -> List[Dict[str, Any]]:
        """The schemas advertised to the model for every tool."""
        return [self.get_tool_schema(name) for name in self.tools.keys()]

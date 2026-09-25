"""
Browser Tool

Tool per operazioni web/browser (semplificato).
"""

from typing import Annotated, Any, Dict, Literal, Optional

import httpx
from loguru import logger
from pydantic import Field

from .base import Tool, ToolArguments, forwarded


class BrowserArgs(ToolArguments):
    url: Annotated[str, Field(description="URL to fetch")]
    method: Annotated[Literal["GET", "POST"] | None, Field(description="HTTP method")] = None
    data: Annotated[Dict[str, Any] | None, Field(description="Data for POST requests")] = None


class BrowserTool(Tool[BrowserArgs]):
    """Tool per operazioni browser/web"""

    arguments = BrowserArgs

    def __init__(self, config: Dict[str, Any]):
        super().__init__(
            name="browser",
            description="Fetch web pages and perform HTTP requests",
        )
        self.config = config
        self.timeout = config.get("tools", {}).get("browser", {}).get("timeout", 30)

    def call(self, args: BrowserArgs) -> Dict[str, Any]:
        return self.execute(**forwarded(args))

    def execute(
        self, url: str, method: str = "GET", data: Optional[Dict[str, Any]] = None, **kwargs: Any
    ) -> Dict[str, Any]:
        """Perform an HTTP request."""
        logger.info(f"HTTP {method} request to {url}")

        try:
            with httpx.Client(timeout=self.timeout) as client:
                if method == "GET":
                    response = client.get(url)
                elif method == "POST":
                    response = client.post(url, json=data)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                return {
                    "success": True,
                    "status_code": response.status_code,
                    "content": response.text,
                    "headers": dict(response.headers),
                }

        except Exception as e:
            logger.error(f"HTTP request error: {e}")
            return {"success": False, "error": str(e)}

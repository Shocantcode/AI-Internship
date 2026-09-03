"""Browser-based news collection for the MCP server."""

from .news_agent import collect_news, format_news_text

__all__ = ["collect_news", "format_news_text"]

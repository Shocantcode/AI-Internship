from dotenv import load_dotenv
from mcp.server import MCPServer

from tools.dag_tools import register_dag_tools
from tools.forecasting_tools import register_forecasting_tools
from tools.news_tools import register_news_tools
from tools.rag_tools import register_rag_tools
from tools.system_tools import register_system_tools
from tools.vision_tools import register_vision_tools
from tools.orchestrator_tools import register_orchestrator_tools

load_dotenv()

mcp = MCPServer("Data AI Hub")

register_system_tools(mcp)
register_rag_tools(mcp)
register_vision_tools(mcp)
register_dag_tools(mcp)
register_forecasting_tools(mcp)
register_news_tools(mcp)
register_orchestrator_tools(mcp)


if __name__ == "__main__":
    mcp.run(
        "streamable-http",
        host="0.0.0.0",
        port=8000,
        streamable_http_path="/mcp",
        stateless_http=True,
    )
import asyncio
import os
import sys

os.environ['RAG_ROOT'] = r'D:\Project\RAG'
sys.path.insert(0, r'D:\Project\MCP')

from mcp.server import MCPServer
from tools.rag_tools import register_rag_tools

mcp = MCPServer('debug')
register_rag_tools(mcp)
tool = mcp._tool_manager.get_tool('search_rag')
print('TOOL', tool.name)

async def main():
    for query in ['silver', 'emas', 'BBRI']:
        try:
            result = await tool.run({'query': query, 'top_k': 3}, None, convert_result=True)
            print('QUERY', query)
            print('RESULT_TYPE', type(result).__name__)
            print(result)
        except Exception:
            import traceback
            print('QUERY', query)
            traceback.print_exc()

asyncio.run(main())

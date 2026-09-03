import asyncio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    async with httpx.AsyncClient(timeout=httpx.Timeout(300)) as http_client:
        async with streamable_http_client('http://localhost:8000/mcp', http_client=http_client) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                for q in ['silver', 'emas', 'BBRI']:
                    print('=== QUERY', q, '===')
                    result = await session.call_tool('search_rag', {'query': q, 'top_k': 3})
                    print(type(result).__name__)
                    print(result.model_dump_json(indent=2) if hasattr(result, 'model_dump_json') else result)

asyncio.run(main())

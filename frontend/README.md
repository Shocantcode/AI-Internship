# signal/desk

Portfolio frontend for the AI Business Intelligence platform.

## Run

Install Node.js 20+ and run:

```powershell
cd frontend
npm install
npm run dev
```

The app uses `VITE_MCP_URL` when present and defaults to
`http://localhost:8000/mcp`. The dashboard remains usable in Demo Mode when
the browser cannot reach the MCP endpoint directly.

## Connected capabilities

- MCP status and tool execution
- Background news collection with `get_news`
- MinIO news knowledge base visibility
- RAG retrieval evidence view
- Vision extraction result view
- Airflow medallion pipeline monitoring
- Forecasting and agent decision views
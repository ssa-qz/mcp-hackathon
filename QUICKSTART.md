# Quickstart: SSA MCP Server

From clone to a connected MCP client in about five minutes.

## 1. Install

Requires Python 3.11+ and Git.

```bash
git clone https://github.com/ssa-qz/mcp-hackathon.git
cd mcp-hackathon
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python scripts/baby_name_ingest.py
```

The ingest step builds `data/names.db` from the bundled public `data/names/yobYYYY.txt` files.
It is safe to rerun: an existing database is kept, and only the summary table is added if missing.
Use `--force` (with the server stopped) for a validated, atomic rebuild.

Optional: `cp .env.example .env` and edit. The defaults work locally.

## 2. Run

```bash
python main.py                                  # stdio, for local MCP clients
python main.py --transport http --port 8080     # streamable HTTP
```

In HTTP mode: MCP endpoint `http://127.0.0.1:8080/mcp`, health `http://127.0.0.1:8080/health`,
version `http://127.0.0.1:8080/version`.

## 3. Connect a client

Use **absolute paths**. The Python interpreter must be the one from your virtual environment.

### Claude Desktop

Edit `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "ssa": {
      "command": "C:\\path\\to\\mcp-hackathon\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\mcp-hackathon\\main.py"]
    }
  }
}
```

Restart Claude Desktop. All 14 SSA tools appear under the tools icon.

### VS Code (MCP extension) / any streamable-HTTP client

Start `python main.py --transport http --port 8080` and add the server URL `http://127.0.0.1:8080/mcp`.

### MCP Inspector

Needs Node.js 18+ and npm. Install the Inspector globally once. On Windows this also puts `mcp-inspector` on your PATH, which the launcher requires:

```powershell
npm install -g @modelcontextprotocol/inspector@2.14.0
```

Then, from the repository root:

```powershell
fastmcp dev inspector main.py:mcp --inspector-version 2.14.0
```

The Inspector prints a local URL with an auth token (`http://127.0.0.1:6274/...`). Open it in your browser. Plain `npx @modelcontextprotocol/inspector` without a global install fails on Windows with `'mcp-inspector' is not recognized`.

### Terminal demo (Azure OpenAI)

The optional [`demo/chat.py`](demo/chat.py) client is used in the video demo. It discovers the
connected server's tools and instructions at startup and prints tool calls just before the
assistant's answer. Azure OpenAI credentials are required for this client, not for the SSA MCP
server itself.

In the project's virtual environment, install the client dependencies:

```bash
python -m pip install "langchain>=1.0.0" "langchain-openai>=1.0.0"
```

Set `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION`, and
`AZURE_OPENAI_DEPLOYMENT_NAME` in your environment or in an uncommitted `.env` file.

From the repository root, start the server in one terminal:

```bash
python main.py --transport http --port 8080
```

In a second terminal, activate the same virtual environment and run:

```bash
python demo/chat.py --mcp-url http://127.0.0.1:8080/mcp --env-file .env
```

The client defaults to the repository's `.env` if you omit `--env-file`. Use synthetic details
for sandbox appointment prompts.

## 4. Try it

- "What were the top three girls' names in 2025?"
- "I live at 10416 S Eastern Ave, Henderson, NV 89052. Which SSA office should I go to, and what are its Tuesday hours?"
- "How much will Social Security benefits rise in January because of the COLA?"
- "Book a card replacement appointment at office A00 next Tuesday at 10:00 AM for Jane Doe, (555) 234-5678."
  The agent should summarize the details and ask you to confirm before booking.

## 5. Verify

```bash
pip install pytest pytest-asyncio ruff
ruff check .
pytest -q
python eval/run_eval.py --skip-live    # ground-truth tool eval, offline
```

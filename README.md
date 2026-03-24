# Agent Memory System

Persistent memory for AI agents using SQLite + FTS5.
Single-file core. Zero dependencies. Optional MCP server and Obsidian sync.

## Features

- **Persistent** — SQLite database survives process restarts
- **Full-text search** — FTS5 engine with ranked results
- **Agent isolation** — each agent gets its own namespace automatically
- **MCP Server** — 12 tools for Claude Code / Claude Desktop
- **Obsidian sync** — optional two-way Markdown bridge
- **CLAUDE.md generator** — auto-inject context into every session
- **Cross-platform** — macOS, Linux, WSL, Windows
- **Zero dependencies** — core uses only Python standard library

## Quick Start

### Option 1: CLI (zero dependencies)

```bash
cp memory.py /usr/local/bin/memory
chmod +x /usr/local/bin/memory

memory store --type semantic --content "Max upload: 50 MB" --project api --importance 5
memory search --query "upload"
memory list --project api --recent 10
memory stats
```

### Option 2: MCP Server (Claude Code / Claude Desktop)

```bash
pip install mcp
```

Add to `~/.claude/settings.json` (Claude Code):

```json
{
  "mcpServers": {
    "agent-memory": {
      "command": "python",
      "args": ["/path/to/memory_mcp.py"]
    }
  }
}
```

For Claude Desktop, use:
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Restart and you'll see 12 tools available.

## MCP Tools

| Tool | Description |
|------|-------------|
| `memory_brief` | **Call first!** Loads critical context at session start |
| `memory_store` | Store a new memory |
| `memory_search` | Full-text search (FTS5) |
| `memory_list` | List with project/type/recency filters |
| `memory_update` | Patch content, importance, or tags |
| `memory_delete` | Remove by ID |
| `memory_stats` | Database statistics |
| `memory_who` | Agent identity and scope |
| `memory_export` | Export to JSON |
| `memory_generate_context` | Generate CLAUDE.md from key memories |
| `memory_sync_to_vault` | SQLite → Obsidian Markdown |
| `memory_sync_from_vault` | Obsidian Markdown → SQLite |

## Memory Types

| Type | When to Use | Examples |
|------|-------------|---------|
| `episodic` | Events, decisions | "Switched to Postgres for JSON support" |
| `semantic` | Facts, knowledge | "Rate limit: 1000 req/min" |
| `procedural` | Workflows, how-tos | "Steps: build > test > deploy" |

## Agent Isolation

Set `AGENT_NAME` and isolation is automatic.

```bash
export AGENT_NAME=health         # Linux / macOS / WSL
$env:AGENT_NAME = "health"       # Windows PowerShell
```

| Role | Write Access | Read Access |
|------|-------------|-------------|
| Regular agent | Own namespace only | Own namespace + global |
| orchestrator | Anywhere | Everything |
| Not set | Anywhere | Everything |

## Obsidian Integration (Optional)

```bash
export OBSIDIAN_VAULT=/path/to/your/vault

# Auto-mirrors every store/update/delete to Markdown
memory store --type semantic --content "API uses JWT" --project api

# Bulk sync
memory sync-to-vault          # SQLite → Markdown
memory sync-from-vault         # Markdown → SQLite
```

## CLAUDE.md Generator

```bash
memory generate-context --output CLAUDE.md
```

Creates a Markdown file from your most important memories. Place it in your
project root and Claude Code reads it automatically every session.

## Database Location

| Platform | Default Path |
|----------|-------------|
| macOS / Linux | `~/.claude/memory/memory.db` |
| Windows | `%APPDATA%\claude\memory\memory.db` |

Override: `export AGENT_MEMORY_DIR=/custom/path`

## File Structure

```
agent-memory/
├── memory.py              # Core: CLI + library (1282 lines)
├── memory_mcp.py          # MCP server (645 lines, requires: pip install mcp)
├── README.md
├── LICENSE
├── .gitignore
├── requirements.txt       # Runtime: empty (stdlib only)
├── requirements-dev.txt   # Dev: pytest, black, mypy, ruff
└── tests/
    ├── __init__.py
    └── test_memory.py     # 58 tests across 8 classes
```

## Testing

```bash
pip install -r requirements-dev.txt
python -m pytest tests/test_memory.py -v               # 58 tests
python -m pytest tests/test_memory.py -v --cov=memory
```

## License

MIT

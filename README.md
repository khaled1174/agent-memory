<div align="center">

<img src="https://raw.githubusercontent.com/khaled1174/agent-memory/main/assets/banner.svg" alt="agent-memory banner" width="900"/>

# Agent Memory System

[![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-58%20passing-brightgreen?style=flat-square)](tests/)
[![Zero Dependencies](https://img.shields.io/badge/Dependencies-Zero-blue?style=flat-square)](requirements.txt)
[![MCP Tools](https://img.shields.io/badge/MCP-12%20tools-purple?style=flat-square)](memory_mcp.py)

**Persistent memory for AI agents using SQLite + FTS5.**  
Single-file core · Zero dependencies · Optional MCP server and Obsidian sync.

</div>

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🗄️ **Persistent** | SQLite database survives process restarts |
| 🔍 **Full-text search** | FTS5 engine with ranked results |
| 🔒 **Agent isolation** | Each agent gets its own namespace automatically |
| 🛠️ **MCP Server** | 12 tools for Claude Code / Claude Desktop |
| 📝 **Obsidian sync** | Optional two-way Markdown bridge |
| 🤖 **CLAUDE.md generator** | Auto-inject context into every session |
| 🌍 **Cross-platform** | macOS, Linux, WSL, Windows |
| ⚡ **Zero dependencies** | Core uses only Python standard library |

---

## 🏗️ Architecture

```mermaid
graph TB
    subgraph Clients["🖥️ Clients"]
        CLI["💻 CLI\n(memory.py)"]
        MCP["🔌 MCP Server\n(memory_mcp.py)"]
        HERMES["🌉 Hermes Bridge\n(hermes/bridge.py)"]
    end

    subgraph Core["⚙️ Core Engine"]
        STORE["📥 Store\nmemory_store()"]
        SEARCH["🔍 Search\nFTS5 Engine"]
        ISO["🔒 Isolation\nNamespace Handler"]
        CTX["📋 Context\nCLAUDE.md Gen"]
    end

    subgraph Storage["💾 Storage"]
        DB[("🗄️ SQLite DB\nmemory.db")]
        FTS[("📑 FTS5 Index")]
    end

    subgraph External["🔗 Integrations"]
        OBS["📓 Obsidian Vault\n(Markdown)"]
        N8N["⚙️ n8n Workflow"]
        TG["📱 Telegram Bot"]
    end

    CLI --> STORE
    CLI --> SEARCH
    MCP --> STORE
    MCP --> SEARCH
    HERMES --> STORE
    HERMES --> SEARCH
    STORE --> ISO
    SEARCH --> ISO
    ISO --> DB
    DB --> FTS
    FTS --> SEARCH
    STORE --> OBS
    OBS --> STORE
    N8N --> HERMES
    TG --> HERMES
    CTX --> DB

    style Clients fill:#1f3558,stroke:#58a6ff,color:#cdd9e5
    style Core fill:#2d1f58,stroke:#bc8cff,color:#cdd9e5
    style Storage fill:#1f3a2d,stroke:#3fb950,color:#cdd9e5
    style External fill:#3a2d1f,stroke:#d29922,color:#cdd9e5
```

---

## 🔄 Memory Lifecycle

```mermaid
sequenceDiagram
    actor Agent as 🤖 AI Agent
    participant CLI as 💻 CLI / MCP
    participant ISO as 🔒 Namespace
    participant DB as 🗄️ SQLite
    participant FTS as 🔍 FTS5 Index
    participant OBS as 📓 Obsidian

    Agent->>CLI: memory store --type semantic --content "..."
    CLI->>ISO: Resolve agent namespace (AGENT_NAME)
    ISO-->>CLI: agent_id = "health" (or global)
    CLI->>DB: INSERT INTO memories (agent_id, type, content, ...)
    DB->>FTS: Auto-index content
    DB-->>CLI: memory_id = 42
    CLI-->>Agent: ✅ Stored (id=42)

    Agent->>CLI: memory search --query "rate limit"
    CLI->>ISO: Resolve agent namespace
    ISO-->>CLI: agent_id
    CLI->>FTS: SELECT * FROM memories_fts WHERE content MATCH "rate limit"
    FTS-->>CLI: Ranked results
    CLI-->>Agent: 📋 Results (ranked by relevance)

    Note over DB,OBS: Optional Obsidian Sync
    DB->>OBS: Sync to Markdown vault
    OBS->>DB: Sync from Markdown vault
```

---

## 🧠 Memory Types

```mermaid
mindmap
  root((🧠 Memory Types))
    📅 episodic
      Events and decisions
      Switched to Postgres
      Deployed v2 on Monday
    📚 semantic
      Facts and knowledge
      Rate limit 1000 req/min
      Max upload 50 MB
    ⚙️ procedural
      Workflows and how-tos
      Steps build test deploy
      Auth flow OAuth2 JWT
```

---

## 🚀 Quick Start

### Option 1: CLI (zero dependencies)

```bash
cp memory.py /usr/local/bin/memory
chmod +x /usr/local/bin/memory

# Store a memory
memory store --type semantic --content "Max upload: 50 MB" --project api --importance 5

# Search memories
memory search --query "upload"

# List recent
memory list --project api --recent 10

# View stats
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

> **Claude Desktop paths:**
> - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
> - Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Restart and you'll see **12 tools** available.

---

## 🛠️ MCP Tools

```mermaid
graph LR
    subgraph Session["📋 Session"]
        T1["memory_brief\nLoad context"]
        T2["memory_who\nAgent identity"]
        T3["memory_stats\nDB statistics"]
    end

    subgraph CRUD["✏️ CRUD"]
        T4["memory_store\nSave memory"]
        T5["memory_update\nPatch memory"]
        T6["memory_delete\nRemove by ID"]
    end

    subgraph Query["🔍 Query & Export"]
        T7["memory_search\nFTS5 full-text"]
        T8["memory_list\nFilter and sort"]
        T9["memory_export\nExport to JSON"]
        T10["memory_generate_context\nBuild CLAUDE.md"]
    end

    subgraph Sync["🔄 Sync"]
        T11["memory_sync_to_vault\nSQLite to Obsidian"]
        T12["memory_sync_from_vault\nObsidian to SQLite"]
    end

    style Session fill:#1f3558,stroke:#58a6ff,color:#cdd9e5
    style CRUD fill:#2d1f58,stroke:#bc8cff,color:#cdd9e5
    style Query fill:#1f3a2d,stroke:#3fb950,color:#cdd9e5
    style Sync fill:#3a2d1f,stroke:#d29922,color:#cdd9e5
```

---

## 🔒 Agent Isolation

```mermaid
graph TD
    ENV["🌍 AGENT_NAME env var"]

    ENV -->|"= 'health'"| HEALTH["🏥 health namespace\nWrite: own only\nRead: own + global"]
    ENV -->|"= 'orchestrator'"| ORCH["🎯 orchestrator\nWrite: anywhere\nRead: everything"]
    ENV -->|"not set"| GLOBAL["🌐 global\nWrite: anywhere\nRead: everything"]

    HEALTH --> DB1[("health::memories")]
    HEALTH -.->|read only| DB2[("global::memories")]
    ORCH --> DB1
    ORCH --> DB2
    ORCH --> DB3[("other::memories")]
    GLOBAL --> DB2

    style ENV fill:#21262d,stroke:#58a6ff,color:#cdd9e5
    style HEALTH fill:#1f3558,stroke:#58a6ff,color:#cdd9e5
    style ORCH fill:#2d1f58,stroke:#bc8cff,color:#cdd9e5
    style GLOBAL fill:#1f3a2d,stroke:#3fb950,color:#cdd9e5
```

```bash
export AGENT_NAME=health   # Linux / macOS / WSL
$env:AGENT_NAME = "health" # Windows PowerShell
```

| Role | Write Access | Read Access |
|------|-------------|-------------|
| Regular agent | Own namespace only | Own + global |
| `orchestrator` | Anywhere | Everything |
| Not set | Anywhere | Everything |

---

## 🌉 Hermes — Cross-Platform Memory

> Two agents collaborate on Slack. You ask on Telegram. The memory carries.

```mermaid
flowchart LR
    SLACK["💬 Slack Agent"] -->|POST| N8N["⚙️ n8n"]
    N8N -->|HTTP| BRIDGE["🌉 Hermes Bridge\nbridge.py :8765"]
    BRIDGE <-->|SQLite| DB[("🗄️ memory.db")]
    TG["📱 Telegram Bot"] <-->|HTTP| BRIDGE
    MCP["🔌 MCP Server"] <-->|SQLite| DB
    CLI["💻 CLI"] <-->|SQLite| DB

    style SLACK fill:#1f3a2d,stroke:#3fb950,color:#cdd9e5
    style N8N fill:#3a2d1f,stroke:#d29922,color:#cdd9e5
    style BRIDGE fill:#2d1f58,stroke:#bc8cff,color:#cdd9e5
    style DB fill:#1f3558,stroke:#58a6ff,color:#cdd9e5
    style TG fill:#1f3558,stroke:#58a6ff,color:#cdd9e5
    style MCP fill:#2d1f58,stroke:#bc8cff,color:#cdd9e5
    style CLI fill:#1f3a2d,stroke:#3fb950,color:#cdd9e5
```

```bash
# 1. Start the bridge
python hermes/bridge.py

# 2. Import hermes/n8n_workflow.json into your n8n instance

# 3. Start the Telegram bot
TELEGRAM_TOKEN=xxx HERMES_URL=http://your-server:8765 python hermes/telegram_bot.py
```

> See [hermes/README.md](hermes/README.md) for full setup, API reference, and VPS deployment.

---

## 📦 Obsidian Integration (Optional)

```bash
export OBSIDIAN_VAULT=/path/to/your/vault

# Auto-mirrors every store/update/delete to Markdown
memory store --type semantic --content "API uses JWT" --project api

# Bulk sync
memory sync-to-vault    # SQLite → Markdown
memory sync-from-vault  # Markdown → SQLite
```

---

## 📋 CLAUDE.md Generator

```bash
memory generate-context --output CLAUDE.md
```

Creates a Markdown file from your most important memories. Place it in your project root and Claude Code reads it automatically every session.

---

## 📁 File Structure

```
agent-memory/
├── memory.py              # Core: CLI + library (1282 lines)
├── memory_mcp.py          # MCP server (645 lines, requires: pip install mcp)
├── assets/
│   └── banner.svg         # Repository banner image
├── hermes/                # Cross-platform memory layer
│   ├── bridge.py          # HTTP gateway (zero new deps)
│   ├── telegram_bot.py    # Telegram recall bot (requires: requests)
│   ├── n8n_workflow.json  # Ready-to-import Slack → memory workflow
│   └── README.md          # Hermes setup guide
├── README.md
├── LICENSE
├── .gitignore
├── requirements.txt       # Runtime: empty (stdlib only)
├── requirements-dev.txt   # Dev: pytest, black, mypy, ruff
└── tests/
    ├── __init__.py
    └── test_memory.py     # 58 tests across 8 classes
```

---

## 🗃️ Database Location

| Platform | Default Path |
|----------|-------------|
| macOS / Linux | `~/.claude/memory/memory.db` |
| Windows | `%APPDATA%\\claude\\memory\\memory.db` |

Override: `export AGENT_MEMORY_DIR=/custom/path`

---

## 🧪 Testing

```bash
pip install -r requirements-dev.txt
python -m pytest tests/test_memory.py -v          # 58 tests
python -m pytest tests/test_memory.py -v --cov=memory
```

---

## 📄 License

[MIT](LICENSE) © khaled1174

---

<div align="center">
<sub>Built with ❤️ for AI agents that need to remember.</sub>
</div>

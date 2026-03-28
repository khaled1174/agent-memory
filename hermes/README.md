# Hermes — Cross-Platform Persistent Memory

Agents write on Slack. You recall on Telegram. The memory carries.

The relationship carries. The context carries.

No vector database. No Redis. No extra infrastructure.  
Just the same SQLite file, shared across platforms, via a lightweight HTTP bridge.

---

## The Problem

Every agent framework in 2026 solves single-agent memory across sessions.  
**Two agents sharing persistent memory across platforms** — that's the gap.

Hermes closes it.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Shared memory.db                      │
│               (SQLite · project=shared)                  │
└───────────────────────┬─────────────────────────────────┘
                        │
              ┌─────────┴─────────┐
              │   Hermes Bridge   │
              │  bridge.py :8765  │
              └──┬───────────┬───┘
                 │           │
        ┌────────┘           └────────┐
        ▼                            ▼
  ┌───────────┐               ┌─────────────┐
  │  n8n      │               │  Telegram   │
  │  Slack    │               │    Bot      │
  │  trigger  │               │ (recall)    │
  └───────────┘               └─────────────┘
  Agents write                  You recall
```

**Flow:**
1. Slack agent says something → n8n catches it → POSTs to `/store`
2. Bridge writes it to `shared` project in memory.db
3. You open Telegram and ask: *"what code did we work on?"*
4. Bot calls `/search` → returns results from the same DB
5. Memory carries. Relationship carries. Context carries.

---

## Components

| File | Purpose |
|------|---------|
| `bridge.py` | HTTP gateway — zero new deps, uses stdlib only |
| `telegram_bot.py` | Telegram bot — requires `pip install requests` |
| `n8n_workflow.json` | Ready-to-import n8n workflow for Slack integration |

---

## Setup

### Step 1 — Start the Bridge

```bash
# From repo root
python hermes/bridge.py

# Custom port / host
python hermes/bridge.py --port 8765 --host 0.0.0.0

# With auth token (recommended in production)
HERMES_TOKEN=my-secret python hermes/bridge.py
```

The bridge runs on port `8765` by default and shares the same `memory.db`  
as the rest of the Agent Memory System.

**On your VPS (148.230.124.73):**
```bash
# Run as a background service
nohup python hermes/bridge.py > hermes-bridge.log 2>&1 &

# Or with systemd — see Deployment section below
```

### Step 2 — Import the n8n Workflow

1. Open your n8n instance
2. **Import** → select `hermes/n8n_workflow.json`
3. Set environment variables in n8n:
   ```
   HERMES_HOST = 148.230.124.73   (your VPS IP)
   HERMES_PORT = 8765
   ```
4. If using `HERMES_TOKEN`, add an `Authorization: Bearer <token>` header  
   to the **Hermes — Store Memory** node
5. Connect your Slack credentials and **activate** the workflow

### Step 3 — Start the Telegram Bot

```bash
pip install requests

TELEGRAM_TOKEN=xxx \
HERMES_URL=http://148.230.124.73:8765 \
HERMES_TOKEN=my-secret \
python hermes/telegram_bot.py
```

---

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/search <query>` | Search shared memory |
| `/recall <query>` | Same as /search |
| `/remember <text>` | Store a memory from Telegram |
| `/recent [N]` | Last N memories (default 5) |
| `/stats` | Database statistics |
| `/health` | Bridge connection status |
| `/help` | Help message |

Or just type naturally — the bot searches memory automatically.

---

## Bridge API Reference

All endpoints return JSON.

### `GET /health`
```json
{ "status": "ok", "service": "hermes-bridge", "db": "/path/to/memory.db" }
```

### `POST /store`
```json
{
  "content": "Icarus and Daedalus argued about the WebSocket broker architecture",
  "agent": "icarus",
  "type": "episodic",
  "project": "shared",
  "importance": 7,
  "source": "slack",
  "tags": ["websocket", "architecture", "code-review"]
}
```
Response: `{ "id": 42, "agent": "icarus", "project": "shared" }`

### `GET /search?q=websocket&limit=5`
```json
{
  "results": [...],
  "count": 3,
  "query": "websocket"
}
```

### `GET /list?limit=10`
### `GET /stats`
### `POST /webhook/slack` — Slack Events API (handles challenge automatically)
### `POST /webhook/n8n` — Same as `/store`, for n8n HTTP Request node

---

## Deployment (systemd on VPS)

Create `/etc/systemd/system/hermes-bridge.service`:

```ini
[Unit]
Description=Hermes Bridge — Cross-platform memory gateway
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/path/to/agent-memory
Environment=HERMES_TOKEN=your-secret-token
Environment=HERMES_PORT=8765
ExecStart=/usr/bin/python3 hermes/bridge.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable hermes-bridge
sudo systemctl start hermes-bridge
sudo systemctl status hermes-bridge
```

---

## Security Notes

- Set `HERMES_TOKEN` in production — prevents unauthorized writes to your memory DB
- The Slack webhook endpoint (`/webhook/slack`) handles URL verification without auth (required by Slack), but filters bot messages and validates event structure
- Use `ALLOWED_CHATS` in the Telegram bot to restrict who can query memory
- Consider running behind nginx with HTTPS for VPS deployments

---

## How It Extends Agent Memory System

Hermes adds one layer on top of the existing system:

```
Agent Memory System (core)
├── memory.py          ← SQLite + FTS5 + agent isolation
├── memory_mcp.py      ← 12 MCP tools for Claude Code
└── hermes/            ← Cross-platform layer (NEW)
    ├── bridge.py      ← HTTP gateway (zero new deps)
    ├── telegram_bot.py← Recall from Telegram
    └── n8n_workflow.json ← Slack integration
```

The `shared` project in memory.db is the common ground.  
Any platform that can send HTTP can write to it.  
Any platform that can receive HTTP can read from it.

---

## Security & Production Features

### Rate Limiting

Built-in per-IP sliding window rate limiter — no Redis needed.

```bash
HERMES_RATE_LIMIT=60 python hermes/bridge.py   # default: 60 req/min/IP
```

Rate-limited requests receive `429 Too Many Requests` with `Retry-After` and `X-RateLimit-*` headers.

### HTTPS Enforcement

```bash
HERMES_REQUIRE_HTTPS=true python hermes/bridge.py
```

Respects `X-Forwarded-Proto` and `X-Real-IP` headers for nginx/Cloudflare deployments.

### Structured Logging

Both bridge and bot write structured logs to files:
- `hermes_bridge.log` — bridge requests, errors, rate limit hits
- `hermes_telegram_bot.log` — bot activity, bridge errors, signal handling

### Graceful Shutdown

The Telegram bot handles `SIGINT` and `SIGTERM` cleanly — safe for systemd and Docker.

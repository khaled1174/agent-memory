#!/usr/bin/env python3
"""
Hermes Telegram Bot — Query shared agent memory from Telegram.

Agents write on Slack. You recall on Telegram. Memory carries.

Requires:
    pip install requests

Environment:
    TELEGRAM_TOKEN     Bot token from @BotFather (required)
    HERMES_URL         Bridge URL (default: http://localhost:8765)
    HERMES_TOKEN       Bridge auth token (if bridge uses one)
    ALLOWED_CHATS      Comma-separated chat IDs allowlist (optional)
    DEFAULT_AGENT      Agent name to store Telegram memories as (default: telegram-user)

Usage:
    TELEGRAM_TOKEN=xxx HERMES_URL=http://148.230.124.73:8765 python hermes/telegram_bot.py
"""

import os
import sys
import time
import json
import logging
import signal
from typing import Optional

# ─── Logging Setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('hermes_telegram_bot.log', mode='a', encoding='utf-8')
    ]
)
logger = logging.getLogger("hermes-telegram-bot")

try:
    import requests
except ImportError:
    logger.error("requests not installed. Run: pip install requests")
    sys.exit(1)

# ─── Config ───────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
HERMES_URL     = os.environ.get("HERMES_URL", "http://localhost:8765").rstrip("/")
HERMES_TOKEN   = os.environ.get("HERMES_TOKEN", "")
ALLOWED_CHATS  = set(filter(None, os.environ.get("ALLOWED_CHATS", "").split(",")))
DEFAULT_AGENT  = os.environ.get("DEFAULT_AGENT", "telegram-user")

HELP_TEXT = """
🧠 *Hermes Memory Bot*

I give you cross-platform memory recall.
Agents write on Slack. You ask here. Memory carries.

*Commands:*
`/search <query>` — Search shared memory
`/recall <query>` — Same as /search
`/remember <text>` — Store a memory from Telegram
`/recent [N]` — Last N memories (default 5)
`/stats` — Database statistics
`/health` — Bridge connection status
`/help` — This message

*Natural language:*
Just type your question — I'll search memory automatically.

_Example: what code did we work on yesterday?_
"""


# ─── Bridge client ────────────────────────────────────────────────────────────

class HermesBridge:
    def __init__(self, url: str, token: str = ""):
        self.url     = url
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        logger.info(f"Bridge client initialized: {url}")

    def store(self, content: str, agent: str = DEFAULT_AGENT,
              type_: str = "episodic", importance: int = 5,
              tags: Optional[list] = None) -> Optional[int]:
        payload = {
            "content":    content,
            "agent":      agent,
            "type":       type_,
            "importance": max(1, min(5, importance)),
            "source":     "telegram",
            "tags":       tags or [],
        }
        try:
            r = requests.post(f"{self.url}/store", json=payload,
                              headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json().get("id")
        except requests.exceptions.Timeout:
            logger.error(f"Bridge store timeout after 10s")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Bridge store error: {e}")
            return None

    def search(self, query: str, limit: int = 5) -> list:
        try:
            r = requests.get(f"{self.url}/search",
                             params={"q": query, "limit": limit},
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json().get("results", [])
        except requests.exceptions.Timeout:
            logger.error(f"Bridge search timeout after 10s")
            return []
        except requests.exceptions.RequestException as e:
            logger.error(f"Bridge search error: {e}")
            return []

    def list(self, limit: int = 5) -> list:
        try:
            r = requests.get(f"{self.url}/list",
                             params={"limit": limit},
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json().get("items", [])
        except requests.exceptions.Timeout:
            logger.error(f"Bridge list timeout after 10s")
            return []
        except requests.exceptions.RequestException as e:
            logger.error(f"Bridge list error: {e}")
            return []

    def stats(self) -> dict:
        try:
            r = requests.get(f"{self.url}/stats",
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Bridge stats error: {e}")
            return {"error": str(e)}

    def health(self) -> dict:
        try:
            r = requests.get(f"{self.url}/health",
                             headers=self.headers, timeout=5)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Bridge health check error: {e}")
            return {"error": str(e)}


# ─── Bot ─────────────────────────────────────────────────────────────────────

class HermesTelegramBot:
    def __init__(self, token: str, bridge: HermesBridge):
        self.token  = token
        self.api    = f"https://api.telegram.org/bot{token}"
        self.bridge = bridge
        self.offset = 0
        self.running = True
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        logger.info("Telegram bot initialized")
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    # ── Telegram API ─────────────────────────────────────────────────────────

    def get_updates(self) -> list:
        try:
            r = requests.get(
                f"{self.api}/getUpdates",
                params={"offset": self.offset, "timeout": 30,
                        "allowed_updates": json.dumps(["message"])},
                timeout=35,
            )
            r.raise_for_status()
            return r.json().get("result", [])
        except requests.exceptions.Timeout:
            logger.debug("Long poll timeout, continuing...")
            return []
        except requests.exceptions.RequestException as e:
            logger.error(f"get_updates error: {e}")
            time.sleep(3)
            return []

    def send(self, chat_id: int, text: str):
        try:
            r = requests.post(
                f"{self.api}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=10,
            )
            r.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"send error to chat {chat_id}: {e}")

    def send_typing(self, chat_id: int):
        try:
            requests.post(f"{self.api}/sendChatAction",
                          json={"chat_id": chat_id, "action": "typing"},
                          timeout=5)
        except requests.exceptions.RequestException:
            pass  # Non-critical, ignore errors

    # ── Message handling ──────────────────────────────────────────────────────

    def handle(self, message: dict):
        chat_id  = message["chat"]["id"]
        user     = message.get("from", {})
        username = user.get("username") or user.get("first_name") or "unknown"
        text     = (message.get("text") or "").strip()

        if not text:
            return

        # Allowlist check
        if ALLOWED_CHATS and str(chat_id) not in ALLOWED_CHATS:
            logger.warning(f"Unauthorized access attempt from chat {chat_id}")
            self.send(chat_id, "⚠️ This bot is private. Unauthorized chat.")
            return

        print(f"[Bot] @{username} ({chat_id}): {text[:80]}")
        logger.info(f"Message from @{username} ({chat_id}): {text[:80]}")
        self.send_typing(chat_id)

        # ── Command dispatch ──────────────────────────────────────────────────

        lower = text.lower()

        if lower in ("/start", "/help"):
            self.send(chat_id, HELP_TEXT)

        elif lower.startswith(("/search ", "/recall ")):
            query = text.split(" ", 1)[1].strip()
            self._cmd_search(chat_id, query)

        elif lower.startswith("/remember "):
            content = text.split(" ", 1)[1].strip()
            self._cmd_remember(chat_id, content, agent=username)

        elif lower.startswith("/recent"):
            parts = text.split()
            n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 5
            self._cmd_recent(chat_id, n)

        elif lower == "/stats":
            self._cmd_stats(chat_id)

        elif lower == "/health":
            self._cmd_health(chat_id)

        elif text.startswith("/"):
            self.send(chat_id, "Unknown command. Type /help for the list.")

        else:
            # Natural language → treat as search
            self._cmd_search(chat_id, text)

    # ── Commands ──────────────────────────────────────────────────────────────

    def _cmd_search(self, chat_id: int, query: str):
        results = self.bridge.search(query, limit=5)
        if not results:
            self.send(chat_id, f"🔍 No memories found for: _{query}_")
            return

        lines = [f"🧠 *Results for:* _{query}_\n"]
        for i, r in enumerate(results, 1):
            content = r.get("content", "")[:140]
            mtype   = r.get("type", "?")
            tags    = r.get("tags", "")
            agent   = r.get("agent_id", "")
            ts      = r.get("created_at", "")[:10]

            lines.append(f"*{i}.* `[{mtype}]` {content}")
            meta = []
            if agent:
                meta.append(f"agent: {agent}")
            if tags:
                meta.append(f"tags: {tags}")
            if ts:
                meta.append(ts)
            if meta:
                lines.append(f"   _{'  |  '.join(meta)}_")
            lines.append("")

        self.send(chat_id, "\n".join(lines))

    def _cmd_remember(self, chat_id: int, content: str, agent: str = DEFAULT_AGENT):
        mem_id = self.bridge.store(content, agent=agent)
        if mem_id:
            self.send(chat_id, f"✅ Stored (id: `{mem_id}`)")
        else:
            self.send(chat_id, "❌ Failed to store. Is the bridge running?")

    def _cmd_recent(self, chat_id: int, n: int = 5):
        items = self.bridge.list(limit=n)
        if not items:
            self.send(chat_id, "No memories yet.")
            return

        lines = [f"🧠 *Last {len(items)} memories:*\n"]
        for item in items:
            content = item.get("content", "")[:100]
            mtype   = item.get("type", "?")
            ts      = item.get("created_at", "")[:10]
            lines.append(f"• `[{mtype}]` {content}")
            if ts:
                lines.append(f"   _{ts}_")
        self.send(chat_id, "\n".join(lines))

    def _cmd_stats(self, chat_id: int):
        data = self.bridge.stats()
        if "error" in data:
            self.send(chat_id, f"❌ Bridge error: {data['error']}")
            return
        self.send(chat_id,
            f"📊 *Memory Stats*\n\n"
            f"Total memories: `{data.get('total_memories', '?')}`\n"
            f"Shared memories: `{data.get('shared_memories', '?')}`\n"
            f"DB: `{data.get('db', '?')}`"
        )

    def _cmd_health(self, chat_id: int):
        data = self.bridge.health()
        if "error" in data:
            self.send(chat_id, f"❌ Bridge unreachable at `{self.bridge.url}`\n\n`{data['error']}`")
        else:
            self.send(chat_id,
                f"✅ Bridge: `{data.get('status', 'ok')}`\n"
                f"DB: `{data.get('db', '?')}`\n"
                f"URL: `{self.bridge.url}`"
            )

    # ── Loop ──────────────────────────────────────────────────────────────────

    def run(self):
        logger.info("="*60)
        logger.info("Hermes Telegram Bot Starting")
        logger.info("="*60)
        logger.info(f"Bridge: {self.bridge.url}")
        if ALLOWED_CHATS:
            logger.info(f"Allowed chats: {', '.join(ALLOWED_CHATS)}")
        logger.info(f"Log file: hermes_telegram_bot.log")
        logger.info("-"*60)

        # Health check on startup
        h = self.bridge.health()
        if "error" in h:
            logger.warning(f"Bridge unreachable: {h['error']}")
            logger.info("Start the bridge first: python hermes/bridge.py")
        else:
            logger.info(f"✅ Bridge connected — DB: {h.get('db', '?')}")

        logger.info("Polling for messages...")
        logger.info("="*60)

        while self.running:
            try:
                updates = self.get_updates()
                for update in updates:
                    self.offset = update["update_id"] + 1
                    if "message" in update:
                        try:
                            self.handle(update["message"])
                        except Exception as e:
                            logger.error(f"Error handling message: {e}", exc_info=True)

                if not updates:
                    time.sleep(1)
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                time.sleep(5)
        
        logger.info("Bot shutdown complete")


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN environment variable is required.")
        logger.info("Get your token from @BotFather on Telegram.")
        sys.exit(1)

    bridge = HermesBridge(HERMES_URL, HERMES_TOKEN)
    bot    = HermesTelegramBot(TELEGRAM_TOKEN, bridge)
    bot.run()

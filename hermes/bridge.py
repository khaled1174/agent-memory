#!/usr/bin/env python3
"""
Hermes Bridge — HTTP gateway to Agent Memory System
Enables cross-platform agents (Slack, Telegram, any webhook) to share memory.

Architecture:
    Slack  → n8n → POST /store  → memory.db
    Telegram bot  → GET /search → memory.db
    Any agent     → POST /store → memory.db (same DB, shared project)

Usage:
    python hermes/bridge.py
    python hermes/bridge.py --port 8765 --host 0.0.0.0

Environment:
    AGENT_MEMORY_DIR   Override memory DB path (default: ~/.claude/memory/)
    HERMES_TOKEN       Optional Bearer token for auth (recommended in production)
    HERMES_PORT        Port (default: 8765)
    HERMES_HOST        Host (default: 0.0.0.0)
    HERMES_RATE_LIMIT  Max requests per minute per IP (default: 60)
    HERMES_REQUIRE_HTTPS  Set to 'true' to enforce HTTPS in production
"""

import json
import os
import sys
import argparse
import logging
import ssl
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from collections import defaultdict
from time import time
from typing import Dict, List

# ─── Logging Setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('hermes_bridge.log', mode='a', encoding='utf-8')
    ]
)
logger = logging.getLogger("hermes-bridge")

# Run bridge as orchestrator — full read/write across all namespaces
os.environ.setdefault("AGENT_NAME", "orchestrator")

# Allow importing memory.py from parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import memory as mem

HERMES_TOKEN = os.environ.get("HERMES_TOKEN", "")
HERMES_RATE_LIMIT = int(os.environ.get("HERMES_RATE_LIMIT", "60"))
HERMES_REQUIRE_HTTPS = os.environ.get("HERMES_REQUIRE_HTTPS", "").lower() in ("true", "1", "yes")
SHARED_PROJECT = "shared"


# ─── Rate Limiter ────────────────────────────────────────────────────────────

class RateLimiter:
    """
    Simple in-memory rate limiter using sliding window algorithm.
    Tracks requests per IP address within a time window.
    """
    
    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: Dict[str, List[float]] = defaultdict(list)
        logger.info(f"Rate limiter initialized: {max_requests} requests per {window_seconds}s per IP")
    
    def is_allowed(self, ip: str) -> bool:
        """
        Check if the IP is allowed to make a request.
        Returns True if allowed, False if rate limited.
        """
        now = time()
        # Clean old requests outside the window
        self.requests[ip] = [
            t for t in self.requests[ip] 
            if now - t < self.window_seconds
        ]
        
        if len(self.requests[ip]) >= self.max_requests:
            logger.warning(f"Rate limit exceeded for IP: {ip} ({len(self.requests[ip])} requests)")
            return False
        
        self.requests[ip].append(now)
        return True
    
    def get_remaining(self, ip: str) -> int:
        """Get remaining requests allowed for this IP."""
        now = time()
        self.requests[ip] = [
            t for t in self.requests[ip] 
            if now - t < self.window_seconds
        ]
        return max(0, self.max_requests - len(self.requests[ip]))

# Global rate limiter instance
rate_limiter = RateLimiter(max_requests=HERMES_RATE_LIMIT)


# ─── Handler ─────────────────────────────────────────────────────────────────

class BridgeHandler(BaseHTTPRequestHandler):

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _check_https(self) -> bool:
        """
        Check if the connection is secure (HTTPS) in production.
        Returns True if HTTPS is not required or if connection is secure.
        """
        if not HERMES_REQUIRE_HTTPS:
            return True
        
        # Check for X-Forwarded-Proto header (set by reverse proxy like nginx)
        forwarded_proto = self.headers.get("X-Forwarded-Proto", "").lower()
        if forwarded_proto == "https":
            return True
        
        # Check if connection is directly HTTPS
        if isinstance(self.connection, ssl.SSLSocket):
            return True
        
        logger.warning(f"HTTPS required but connection is not secure from {self.client_address[0]}")
        return False

    def _authorized(self) -> bool:
        if not HERMES_TOKEN:
            return True
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {HERMES_TOKEN}"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _respond(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)
    
    def _respond_rate_limited(self, ip: str):
        """Respond with 429 Too Many Requests."""
        remaining = rate_limiter.get_remaining(ip)
        retry_after = 60  # seconds
        
        body = json.dumps({
            "error": "Rate limit exceeded",
            "retry_after": retry_after,
            "remaining": remaining,
            "limit": HERMES_RATE_LIMIT
        }).encode("utf-8")
        
        self.send_response(429)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Retry-After", str(retry_after))
        self.send_header("X-RateLimit-Limit", str(HERMES_RATE_LIMIT))
        self.send_header("X-RateLimit-Remaining", str(remaining))
        self.end_headers()
        self.wfile.write(body)

    def _get_conn(self):
        conn = mem.get_connection()
        mem.init_db(conn)
        return conn

    def log_message(self, fmt, *args):
        """Override to use logging module instead of print."""
        client_ip = self.client_address[0] if self.client_address else "unknown"
        logger.info(f"{client_ip} - {fmt % args}")
    
    def _get_client_ip(self) -> str:
        """Get the real client IP, considering reverse proxy headers."""
        # Check X-Forwarded-For header (set by nginx, cloudflare, etc.)
        forwarded_for = self.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            # Take the first IP in the chain (original client)
            return forwarded_for.split(",")[0].strip()
        
        # Check X-Real-IP header (set by some proxies)
        real_ip = self.headers.get("X-Real-IP", "")
        if real_ip:
            return real_ip
        
        # Fall back to direct connection IP
        return self.client_address[0] if self.client_address else "unknown"

    # ── Routing ───────────────────────────────────────────────────────────────

    def do_GET(self):
        # Get client IP for rate limiting
        client_ip = self._get_client_ip()
        
        # Check HTTPS requirement
        if not self._check_https():
            self._respond(403, {"error": "HTTPS required in production"})
            return
        
        # Check rate limit
        if not rate_limiter.is_allowed(client_ip):
            self._respond_rate_limited(client_ip)
            return
        
        if not self._authorized():
            self._respond(401, {"error": "Unauthorized"})
            return

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        routes = {
            "/health":  self._health,
            "/search":  lambda: self._search(params),
            "/list":    lambda: self._list(params),
            "/stats":   self._stats,
        }
        handler = routes.get(parsed.path)
        if handler:
            handler()
        else:
            self._respond(404, {"error": "Not found"})

    def do_POST(self):
        # Get client IP for rate limiting
        client_ip = self._get_client_ip()
        
        # Check HTTPS requirement
        if not self._check_https():
            self._respond(403, {"error": "HTTPS required in production"})
            return
        
        # Check rate limit (skip for Slack URL verification)
        parsed = urlparse(self.path)
        
        # Slack sends URL-verification challenge without auth — handle first
        if parsed.path == "/webhook/slack":
            data = self._read_json()
            if data.get("type") == "url_verification":
                # Allow URL verification without rate limiting
                self._respond(200, {"challenge": data.get("challenge", "")})
                return
            
            # For actual events, check rate limit
            if not rate_limiter.is_allowed(client_ip):
                self._respond_rate_limited(client_ip)
                return
            
            self._slack_webhook(data)
            return
        
        # Check rate limit for other POST requests
        if not rate_limiter.is_allowed(client_ip):
            self._respond_rate_limited(client_ip)
            return

        if not self._authorized():
            self._respond(401, {"error": "Unauthorized"})
            return

        routes = {
            "/store":         self._store,
            "/webhook/n8n":   self._n8n_webhook,
        }
        handler = routes.get(parsed.path)
        if handler:
            logger.info(f"POST {parsed.path} from {client_ip}")
            handler()
        else:
            self._respond(404, {"error": "Not found"})

    # ── Endpoints ─────────────────────────────────────────────────────────────

    def _health(self):
        self._respond(200, {
            "status": "ok",
            "service": "hermes-bridge",
            "db": str(mem.DB_PATH),
            "project": SHARED_PROJECT,
        })

    def _store(self):
        data = self._read_json()

        content = (data.get("content") or "").strip()
        if not content:
            self._respond(400, {"error": "content is required"})
            return

        agent   = (data.get("agent") or "hermes").strip()
        type_   = data.get("type", "episodic")
        project = data.get("project", SHARED_PROJECT)
        importance = max(1, min(5, int(data.get("importance", 5))))
        source  = data.get("source", "api")         # 'slack', 'telegram', 'api'

        # Build tags string — always include source
        tags_list = data.get("tags", [])
        if isinstance(tags_list, list):
            tags_list = [t for t in tags_list if t]
        if source and source not in tags_list:
            tags_list.append(source)
        tags_str = ",".join(tags_list)

        # Prefix content with agent name so cross-platform context is visible
        full_content = f"[{agent}] {content}"

        try:
            conn   = self._get_conn()
            mem_id = mem.store(conn, type_, full_content, project, tags_str, importance)
            conn.close()
            self._respond(200, {"id": mem_id, "agent": agent, "project": project})
        except mem.MemorySystemError as e:
            self._respond(500, {"error": str(e)})

    def _search(self, params: dict):
        query   = (params.get("q",       [""])[0]).strip()
        project = params.get("project",  [SHARED_PROJECT])[0]
        type_   = params.get("type",     [None])[0]
        limit   = int(params.get("limit", ["10"])[0])

        if not query:
            self._respond(400, {"error": "q parameter required"})
            return

        try:
            conn    = self._get_conn()
            results = mem.search(conn, query, project=project, type_=type_, limit=limit)
            conn.close()
            self._respond(200, {"results": results, "count": len(results), "query": query})
        except mem.MemorySystemError as e:
            self._respond(500, {"error": str(e)})

    def _list(self, params: dict):
        project = params.get("project", [SHARED_PROJECT])[0]
        type_   = params.get("type",    [None])[0]
        limit   = int(params.get("limit", ["20"])[0])
        recent  = params.get("recent",  [None])[0]
        if recent:
            recent = int(recent)

        try:
            conn  = self._get_conn()
            items = mem.list_memories(conn, project=project, type_=type_,
                                      recent=recent, limit=limit)
            conn.close()
            self._respond(200, {"items": items, "count": len(items)})
        except mem.MemorySystemError as e:
            self._respond(500, {"error": str(e)})

    def _stats(self):
        try:
            conn = self._get_conn()
            row  = conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN project=? THEN 1 ELSE 0 END) AS shared "
                "FROM memories", (SHARED_PROJECT,)
            ).fetchone()
            conn.close()
            self._respond(200, {
                "total_memories": row["total"],
                "shared_memories": row["shared"],
                "db": str(mem.DB_PATH),
            })
        except Exception as e:
            self._respond(500, {"error": str(e)})

    # ── Webhook handlers ──────────────────────────────────────────────────────

    def _slack_webhook(self, data: dict):
        """
        Handles Slack Events API.
        message.channels events → stored in shared memory
        """
        event = data.get("event", {})
        if event.get("type") == "message" and "bot_id" not in event:
            text    = (event.get("text") or "").strip()
            user    = event.get("user", "slack-user")
            channel = event.get("channel", "unknown")

            if text:
                content    = f"[Slack #{channel}] {text}"
                tags_str   = f"slack,{channel}"
                try:
                    conn = self._get_conn()
                    mem.store(conn, "episodic", f"[{user}] {content}",
                              SHARED_PROJECT, tags_str, 5)
                    conn.close()
                    logger.info(f"Stored Slack message from {user} in #{channel}")
                except mem.MemorySystemError as e:
                    logger.error(f"Slack store error: {e}", exc_info=True)

        self._respond(200, {"ok": True})

    def _n8n_webhook(self):
        """
        Generic n8n webhook — expects the same payload as /store.
        n8n HTTP Request node → POST /webhook/n8n
        """
        self._store()


# ─── Server ───────────────────────────────────────────────────────────────────

def run(host: str = "0.0.0.0", port: int = 8765):
    # Ensure DB is initialized on startup
    conn = mem.get_connection()
    mem.init_db(conn)
    conn.close()

    server = HTTPServer((host, port), BridgeHandler)
    
    # Log startup information
    auth_status = "✅ Token auth enabled" if HERMES_TOKEN else "⚠️  No token (open access)"
    https_status = "🔒 HTTPS required" if HERMES_REQUIRE_HTTPS else "⚠️  HTTP allowed"
    rate_limit_status = f"🛡️  Rate limit: {HERMES_RATE_LIMIT} req/min/IP"
    
    logger.info(f"="*60)
    logger.info(f"Hermes Bridge Starting")
    logger.info(f"="*60)
    logger.info(f"Running on http://{host}:{port}")
    logger.info(f"DB: {mem.DB_PATH}")
    logger.info(f"Auth: {auth_status}")
    logger.info(f"HTTPS: {https_status}")
    logger.info(f"Rate Limiting: {rate_limit_status}")
    logger.info(f"Log file: hermes_bridge.log")
    logger.info(f"-"*60)
    logger.info(f"Endpoints:")
    logger.info(f"   GET  /health         — Status check")
    logger.info(f"   GET  /search?q=...   — Full-text search memories")
    logger.info(f"   GET  /list           — List memories")
    logger.info(f"   GET  /stats          — DB statistics")
    logger.info(f"   POST /store          — Store a memory")
    logger.info(f"   POST /webhook/slack  — Slack Events API webhook")
    logger.info(f"   POST /webhook/n8n    — n8n webhook (same as /store)")
    logger.info(f"="*60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
        server.shutdown()
        logger.info("Hermes Bridge stopped gracefully")
    except Exception as e:
        logger.critical(f"Server error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hermes Bridge — cross-platform memory gateway")
    parser.add_argument("--port", type=int, default=int(os.environ.get("HERMES_PORT", 8765)))
    parser.add_argument("--host", default=os.environ.get("HERMES_HOST", "0.0.0.0"))
    args = parser.parse_args()
    run(args.host, args.port)

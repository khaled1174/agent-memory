#!/usr/bin/env python3
"""
Agent Memory System for Claude Code
Persistent memory across projects and conversations using SQLite + FTS5.

Platforms: macOS · Linux · WSL · Windows (PowerShell / CMD)
GitHub: https://github.com/khaled1174/agent-memory
License: MIT
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ─── Constants ────────────────────────────────────────────────────────────────

MAX_CONTENT_LENGTH = 50_000
MAX_TAGS_LENGTH    = 500
VALID_TAG_PATTERN  = re.compile(r'^[\w\-,\s]*$')

MEMORY_TYPES  = ("episodic", "semantic", "procedural")
ORCHESTRATOR  = "orchestrator"
IS_WINDOWS    = platform.system() == "Windows"

# (emoji, ascii_fallback)
TYPE_ICONS = {
    "episodic":   ("📅", "[E]"),
    "semantic":   ("📚", "[S]"),
    "procedural": ("⚙️",  "[P]"),
}

logger = logging.getLogger(__name__)


# ─── Configuration ────────────────────────────────────────────────────────────

def _default_db_dir() -> Path:
    """
    Resolve the default DB directory across platforms.

    Priority (highest to lowest):
      1. AGENT_MEMORY_DIR environment variable (always wins)
      2. Windows  -> %APPDATA%\\claude\\memory
      3. Unix     -> ~/.claude/memory
    """
    env = os.environ.get("AGENT_MEMORY_DIR")
    if env:
        return Path(env)

    if IS_WINDOWS:
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "claude" / "memory"
        return Path.home() / "AppData" / "Roaming" / "claude" / "memory"

    return Path.home() / ".claude" / "memory"


DB_DIR  = _default_db_dir()
DB_PATH = DB_DIR / "memory.db"


# ─── Emoji Support ────────────────────────────────────────────────────────────

def _supports_emoji() -> bool:
    """Return True if the current terminal can render emoji."""
    if not IS_WINDOWS:
        return True
    return bool(os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM"))


_EMOJI_OK = _supports_emoji()


def _t(key: str) -> str:
    """Return emoji or ASCII fallback depending on terminal capability."""
    emoji, fallback = TYPE_ICONS[key]
    return emoji if _EMOJI_OK else fallback


# ─── Exceptions ───────────────────────────────────────────────────────────────

class MemorySystemError(Exception):
    """Base exception for Agent Memory errors."""


class ValidationError(MemorySystemError):
    """Raised when input validation fails."""


class DatabaseError(MemorySystemError):
    """Raised when a database operation fails."""


class IsolationError(MemorySystemError):
    """Raised when agent isolation blocks an operation."""


# ─── Validation ───────────────────────────────────────────────────────────────

def validate_content(content: str) -> str:
    """Strip and validate memory content."""
    if not content or not content.strip():
        raise ValidationError("Content cannot be empty.")
    content = content.strip()
    if len(content) > MAX_CONTENT_LENGTH:
        raise ValidationError(
            f"Content too long ({len(content):,} chars). "
            f"Maximum: {MAX_CONTENT_LENGTH:,}."
        )
    return content


def validate_tags(tags: str) -> str:
    """Strip and validate a comma-separated tag string."""
    tags = tags.strip()
    if len(tags) > MAX_TAGS_LENGTH:
        raise ValidationError(
            f"Tags string too long ({len(tags)} chars). "
            f"Maximum: {MAX_TAGS_LENGTH}."
        )
    if tags and not VALID_TAG_PATTERN.match(tags):
        raise ValidationError(
            "Tags may only contain letters, numbers, hyphens, "
            "underscores, commas, and spaces."
        )
    return tags


def validate_project(project: str) -> str:
    """Normalise and validate a project name."""
    project = project.strip().lower()
    if not project:
        return "global"
    if len(project) > 100:
        raise ValidationError("Project name too long (max 100 chars).")
    if not re.match(r'^[\w\-\.]+$', project):
        raise ValidationError(
            "Project name may only contain letters, numbers, "
            "hyphens, underscores, and dots."
        )
    return project


def validate_importance(importance: int) -> int:
    """Ensure importance is in [1, 5]."""
    if not 1 <= importance <= 5:
        raise ValidationError(
            f"Importance must be between 1 and 5 (got {importance})."
        )
    return importance


# ─── Agent Context ────────────────────────────────────────────────────────────

@dataclass
class AgentContext:
    """
    Detects the running agent's identity and enforces memory isolation.

    Usage - zero config required:
      Set ONE environment variable before invoking memory:
        export AGENT_NAME=health          # Linux / macOS / WSL
        $env:AGENT_NAME = "health"        # Windows PowerShell
        set AGENT_NAME=health             # Windows CMD

    Behaviour:
      - Writes are redirected to the agent's own namespace (agent.<n>)
        unless the caller is the orchestrator.
      - Reads are scoped to the agent's namespace + global.
      - The orchestrator role can read and write everywhere.
      - When AGENT_NAME is unset the system runs in single-agent mode
        with no namespace restrictions.
    """

    name:            Optional[str] = None
    is_active:       bool          = False
    namespace:       Optional[str] = None
    is_orchestrator: bool          = False

    @classmethod
    def from_env(cls) -> "AgentContext":
        """Build an AgentContext from the current environment."""
        raw  = (os.environ.get("AGENT_NAME") or "").strip().lower()
        name = re.sub(r"[^a-z0-9_\-]", "", raw) if raw else None
        return cls(
            name            = name,
            is_active       = bool(name),
            namespace       = f"agent.{name}" if name else None,
            is_orchestrator = (name == ORCHESTRATOR),
        )

    def own_namespace(self) -> str:
        """The namespace this agent owns for writes."""
        return self.namespace or "global"

    def resolve_write_project(self, requested: str, *, strict: bool = False) -> str:
        """
        Return the project that should actually receive the write.

        By default (strict=False) regular agents are silently redirected to
        their own namespace.  Pass strict=True to raise IsolationError instead
        — useful for library callers who want explicit failure on cross-writes.
        """
        if not self.is_active:
            return requested
        if self.is_orchestrator:
            return requested
        if requested in ("global", None) or requested != self.namespace:
            if strict:
                raise IsolationError(
                    f"Agent '{self.name}' cannot write to '{requested}'. "
                    f"Only '{self.namespace}' is writable."
                )
            return self.namespace          # type: ignore[return-value]
        return requested

    def read_projects(self, requested: Optional[str] = None) -> Optional[list[str]]:
        """
        Return the list of readable projects, or None (= no restriction).
        """
        if not self.is_active:
            return None
        if self.is_orchestrator:
            return None
        if requested == "all":
            return None
        return [self.namespace, "global"]  # type: ignore[list-item]

    def banner(self) -> str:
        """Short display string for the current agent context."""
        if not self.is_active:
            return ""
        role = "ORCHESTRATOR" if self.is_orchestrator else "AGENT"
        return f"  [{role}: {self.name}  ns={self.namespace}]"


# Module-level singleton resolved once at import time
AGENT = AgentContext.from_env()


# ─── Obsidian Bridge (optional) ──────────────────────────────────────────────

@dataclass
class ObsidianBridge:
    """
    Optional two-way sync between Agent Memory and an Obsidian vault.

    Activation — set ONE environment variable:
      export OBSIDIAN_VAULT=/path/to/your/vault   # Linux / macOS / WSL
      $env:OBSIDIAN_VAULT = "C:\\Users\\me\\vault"  # Windows PowerShell

    When active every store/update/delete automatically mirrors to Markdown
    files inside the vault under a ``memories/`` subfolder.  The files use
    YAML front-matter so Obsidian can filter by type, project, importance,
    and tags.

    When OBSIDIAN_VAULT is unset the bridge is a silent no-op — zero
    overhead and zero dependencies, just like the rest of the system.
    """

    vault_path: Optional[Path] = None
    is_active:  bool           = False
    memory_dir: Optional[Path] = None

    @classmethod
    def from_env(cls) -> "ObsidianBridge":
        raw = (os.environ.get("OBSIDIAN_VAULT") or "").strip()
        if not raw:
            return cls()
        vault = Path(raw)
        return cls(
            vault_path = vault,
            is_active  = True,
            memory_dir = vault / "memories",
        )

    # ── helpers ───────────────────────────────────────────────────────────

    def _ensure_dir(self, project: str) -> Path:
        """Create and return the project sub-folder inside memories/."""
        safe = re.sub(r"[^\w\-\.]", "_", project)
        folder = self.memory_dir / safe            # type: ignore[operator]
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    @staticmethod
    def _slug(content: str, max_len: int = 48) -> str:
        """Derive a filesystem-safe slug from the first line of content."""
        first = content.split("\n", 1)[0].strip()
        slug  = re.sub(r"[^\w\s\-]", "", first).strip().replace(" ", "-").lower()
        return slug[:max_len] or "memory"

    @staticmethod
    def _frontmatter(mem: dict) -> str:
        """Render YAML front-matter block from a memory dict."""
        lines = ["---"]
        lines.append(f"id: {mem['id']}")
        lines.append(f"type: {mem['type']}")
        lines.append(f"project: {mem['project']}")
        if mem.get("agent_id"):
            lines.append(f"agent: {mem['agent_id']}")
        if mem.get("tags"):
            lines.append(f"tags: [{mem['tags']}]")
        lines.append(f"importance: {mem['importance']}")
        lines.append(f"created: {mem['created_at']}")
        lines.append(f"updated: {mem['updated_at']}")
        lines.append("---")
        return "\n".join(lines)

    def _md_path(self, mem: dict) -> Path:
        """Return the Markdown file path for a memory."""
        folder = self._ensure_dir(mem["project"])
        slug   = self._slug(mem["content"])
        return folder / f"{mem['id']:04d}_{slug}.md"

    def _write_md(self, mem: dict) -> Path:
        """Write a single memory as a Markdown file. Returns the path."""
        path = self._md_path(mem)
        body = self._frontmatter(mem) + "\n\n" + mem["content"] + "\n"

        # Add Obsidian tags as footer
        if mem.get("tags"):
            tag_line = " ".join(
                f"#{t.strip()}" for t in mem["tags"].split(",") if t.strip()
            )
            body += f"\n{tag_line}\n"

        path.write_text(body, encoding="utf-8")
        return path

    # ── public hooks (called by store / update / delete) ─────────────────

    def on_store(self, mem: dict) -> None:
        """Mirror a newly stored memory to the vault."""
        if not self.is_active:
            return
        try:
            p = self._write_md(mem)
            logger.info("Obsidian: wrote %s", p)
        except OSError as exc:
            logger.warning("Obsidian write failed: %s", exc)

    def on_update(self, mem: dict) -> None:
        """Re-write the Markdown file after an update."""
        if not self.is_active:
            return
        try:
            # Remove old file (slug may have changed)
            self._remove_by_id(mem["id"], mem["project"])
            p = self._write_md(mem)
            logger.info("Obsidian: updated %s", p)
        except OSError as exc:
            logger.warning("Obsidian update failed: %s", exc)

    def on_delete(self, mem: dict) -> None:
        """Remove the Markdown file when a memory is deleted."""
        if not self.is_active:
            return
        try:
            self._remove_by_id(mem["id"], mem["project"])
            logger.info("Obsidian: removed memory %d", mem["id"])
        except OSError as exc:
            logger.warning("Obsidian delete failed: %s", exc)

    def _remove_by_id(self, memory_id: int, project: str) -> None:
        """Delete any Markdown file whose name starts with the memory ID."""
        safe   = re.sub(r"[^\w\-\.]", "_", project)
        folder = self.memory_dir / safe            # type: ignore[operator]
        if not folder.exists():
            return
        prefix = f"{memory_id:04d}_"
        for f in folder.iterdir():
            if f.name.startswith(prefix) and f.suffix == ".md":
                f.unlink()
                return

    # ── bulk sync commands ────────────────────────────────────────────────

    def sync_to_vault(self, conn: sqlite3.Connection) -> int:
        """Export ALL memories from SQLite to Markdown files. Returns count."""
        if not self.is_active:
            raise MemorySystemError(
                "OBSIDIAN_VAULT is not set. "
                "Export: set the env var to your vault path first."
            )
        rows = conn.execute(
            "SELECT * FROM memories ORDER BY project, id"
        ).fetchall()

        count = 0
        for row in rows:
            self._write_md(dict(row))
            count += 1

        ok = "✅" if _EMOJI_OK else "[OK]"
        print(f"{ok} Synced {count} memories -> {self.memory_dir}")
        return count

    def sync_from_vault(self, conn: sqlite3.Connection) -> int:
        """Import Markdown files (with front-matter) into SQLite. Returns count."""
        if not self.is_active:
            raise MemorySystemError(
                "OBSIDIAN_VAULT is not set. "
                "Import: set the env var to your vault path first."
            )
        if not self.memory_dir or not self.memory_dir.exists():  # type: ignore
            raise MemorySystemError(
                f"Vault memories directory not found: {self.memory_dir}"
            )

        count = 0
        for md_file in sorted(self.memory_dir.rglob("*.md")):
            text = md_file.read_text(encoding="utf-8")
            meta, body = self._parse_frontmatter(text)
            if not meta or "type" not in meta:
                logger.warning("Skipping %s (no valid front-matter)", md_file.name)
                continue

            # Skip if ID already exists
            existing = conn.execute(
                "SELECT id FROM memories WHERE id = ?",
                (meta.get("id", -1),)
            ).fetchone()
            if existing:
                continue

            now = _now()
            conn.execute(
                """INSERT INTO memories
                   (type, content, project, agent_id, tags, importance,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    meta.get("type", "semantic"),
                    body.strip(),
                    meta.get("project", "global"),
                    meta.get("agent", ""),
                    meta.get("tags", ""),
                    int(meta.get("importance", 3)),
                    meta.get("created", now),
                    meta.get("updated", now),
                ),
            )
            count += 1

        conn.commit()
        ok = "✅" if _EMOJI_OK else "[OK]"
        print(f"{ok} Imported {count} new memories from {self.memory_dir}")
        return count

    @staticmethod
    def _parse_frontmatter(text: str) -> tuple[dict, str]:
        """
        Split a Markdown file into (front-matter dict, body text).
        Returns ({}, full_text) if no valid front-matter is found.
        """
        if not text.startswith("---"):
            return {}, text
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}, text

        meta: dict = {}
        for line in parts[1].strip().splitlines():
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            val = val.strip().strip("[]")
            meta[key.strip()] = val

        body = parts[2]
        # Strip Obsidian tag footer (lines starting with #tag)
        clean_lines = []
        for ln in body.splitlines():
            if ln.strip() and all(
                t.startswith("#") for t in ln.strip().split()
            ):
                continue
            clean_lines.append(ln)

        return meta, "\n".join(clean_lines)


# Module-level singleton
VAULT = ObsidianBridge.from_env()


# ─── Database ─────────────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """Open (and create if necessary) the SQLite database."""
    try:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    except sqlite3.Error as exc:
        raise DatabaseError(f"Cannot connect to database: {exc}") from exc
    except OSError as exc:
        raise DatabaseError(f"Cannot create database directory: {exc}") from exc


def init_db(conn: sqlite3.Connection) -> None:
    """Create schema tables, triggers, indexes, and run pending migrations."""
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                type         TEXT    NOT NULL
                                 CHECK(type IN ('episodic','semantic','procedural')),
                content      TEXT    NOT NULL,
                project      TEXT    NOT NULL DEFAULT 'global',
                agent_id     TEXT    NOT NULL DEFAULT '',
                tags         TEXT    NOT NULL DEFAULT '',
                importance   INTEGER NOT NULL DEFAULT 3
                                 CHECK(importance BETWEEN 1 AND 5),
                created_at   TEXT    NOT NULL,
                updated_at   TEXT    NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed TEXT
            );

            CREATE TABLE IF NOT EXISTS _migrations (key TEXT PRIMARY KEY);

            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(content, tags, content=memories, content_rowid=id);

            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content, tags)
                VALUES (new.id, new.content, new.tags);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, tags)
                VALUES ('delete', old.id, old.content, old.tags);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, tags)
                VALUES ('delete', old.id, old.content, old.tags);
                INSERT INTO memories_fts(rowid, content, tags)
                VALUES (new.id, new.content, new.tags);
            END;

            CREATE INDEX IF NOT EXISTS idx_memories_project    ON memories(project);
            CREATE INDEX IF NOT EXISTS idx_memories_type       ON memories(type);
            CREATE INDEX IF NOT EXISTS idx_memories_agent_id   ON memories(agent_id);
            CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at);
        """)
        conn.commit()

        # Safe migration: add agent_id to databases created before v2
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
        if "agent_id" not in cols:
            conn.execute(
                "ALTER TABLE memories ADD COLUMN agent_id TEXT NOT NULL DEFAULT ''"
            )
            conn.commit()

    except sqlite3.Error as exc:
        raise DatabaseError(f"Schema initialisation failed: {exc}") from exc


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _print_memories(rows: list, label: str) -> None:
    search_icon = "🔍" if _EMOJI_OK else "?"
    brain_icon  = "🧠" if _EMOJI_OK else "[MEM]"
    tag_icon    = "🏷️ " if _EMOJI_OK else "tags:"
    date_icon   = "📅" if _EMOJI_OK else "date:"
    star_on     = "★"  if _EMOJI_OK else "*"
    star_off    = "☆"  if _EMOJI_OK else "-"

    if not rows:
        print(f"\n  {search_icon} {label}: no results found\n")
        return

    print(f"\n{'─'*60}")
    count = len(rows)
    print(f"  {brain_icon} {label}  "
          f"({count} {'memory' if count == 1 else 'memories'})")
    print(f"{'─'*60}")
    for r in rows:
        icon  = _t(r["type"]) if r["type"] in TYPE_ICONS else "•"
        stars = star_on * r["importance"] + star_off * (5 - r["importance"])
        print(f"\n  {icon} [{r['id']:>3}] {r['type'].upper():<12} "
              f"project={r['project']}")
        print(f"       {r['content']}")
        if r["tags"]:
            print(f"       {tag_icon} {r['tags']}")
        print(f"       {date_icon} {r['created_at'][:10]}  importance={stars}")
    print(f"{'─'*60}\n")


# ─── Core Operations ──────────────────────────────────────────────────────────

def store(
    conn:       sqlite3.Connection,
    type_:      str,
    content:    str,
    project:    str = "global",
    tags:       str = "",
    importance: int = 3,
) -> int:
    """
    Persist a new memory and return its integer ID.

    The agent context automatically redirects the write to the correct
    namespace — callers never need to manage isolation manually.
    """
    if type_ not in MEMORY_TYPES:
        raise ValidationError(
            f"Invalid type '{type_}'. Valid: {', '.join(MEMORY_TYPES)}"
        )
    content    = validate_content(content)
    tags       = validate_tags(tags)
    project    = validate_project(project)
    importance = validate_importance(importance)

    resolved = AGENT.resolve_write_project(project)
    agent_id = AGENT.name or ""

    if AGENT.is_active and resolved != project and project not in ("global", None):
        logger.info(
            "Write redirected: '%s' -> '%s' (agent isolation)", project, resolved
        )

    now = _now()
    try:
        cur = conn.execute(
            """INSERT INTO memories
               (type, content, project, agent_id, tags, importance,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (type_, content, resolved, agent_id, tags, importance, now, now),
        )
        conn.commit()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to store memory: {exc}") from exc

    mid = cur.lastrowid
    ok  = "✅" if _EMOJI_OK else "[OK]"
    imp = ("⭐" * importance) if _EMOJI_OK else str(importance)
    print(f"{ok} Memory stored [ID: {mid}]")
    print(f"   type={type_} | project={resolved} | importance={imp}")
    if agent_id:
        print(f"   agent={agent_id}")
    if tags:
        print(f"   tags: {tags}")

    # Mirror to Obsidian vault (no-op if OBSIDIAN_VAULT is unset)
    VAULT.on_store({
        "id": mid, "type": type_, "content": content,
        "project": resolved, "agent_id": agent_id, "tags": tags,
        "importance": importance, "created_at": now, "updated_at": now,
    })

    return mid


def search(
    conn:    sqlite3.Connection,
    query:   str,
    project: Optional[str] = None,
    type_:   Optional[str] = None,
    limit:   int = 10,
) -> list[dict]:
    """Full-text search with automatic agent-scoped filtering."""
    if not query or not query.strip():
        raise ValidationError("Search query cannot be empty.")

    query   = query.strip()
    limit   = max(1, min(limit, 100))
    allowed = AGENT.read_projects(project)

    try:
        if allowed is not None:
            ph  = ",".join("?" * len(allowed))
            sql = f"""
                SELECT m.*, rank
                FROM memories m
                JOIN memories_fts fts ON m.id = fts.rowid
                WHERE memories_fts MATCH ?
                  AND m.project IN ({ph})
            """
            params: list = [query, *allowed]
        else:
            sql    = """
                SELECT m.*, rank
                FROM memories m
                JOIN memories_fts fts ON m.id = fts.rowid
                WHERE memories_fts MATCH ?
            """
            params = [query]
            if project and project != "all":
                sql += " AND (m.project = ? OR m.project = 'global')"
                params.append(project)

        if type_:
            if type_ not in MEMORY_TYPES:
                raise ValidationError(f"Invalid type '{type_}'.")
            sql += " AND m.type = ?"
            params.append(type_)

        sql += " ORDER BY rank, m.importance DESC, m.created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE memories SET access_count = access_count + 1, "
                "last_accessed = ? WHERE id = ?",
                (_now(), row["id"]),
            )
        conn.commit()

    except sqlite3.Error as exc:
        raise DatabaseError(f"Search failed: {exc}") from exc

    label = f"Search: '{query}'" + (AGENT.banner() if AGENT.is_active else "")
    _print_memories(rows, label)
    return [dict(r) for r in rows]


def list_memories(
    conn:    sqlite3.Connection,
    project: Optional[str] = None,
    type_:   Optional[str] = None,
    recent:  Optional[int] = None,
    limit:   int = 20,
) -> list[dict]:
    """List memories with optional project / type / recency filters."""
    allowed = AGENT.read_projects(project)

    try:
        if allowed is not None:
            ph     = ",".join("?" * len(allowed))
            sql    = f"SELECT * FROM memories WHERE project IN ({ph})"
            params: list = list(allowed)
        else:
            sql    = "SELECT * FROM memories WHERE 1=1"
            params = []
            if project and project != "all":
                sql += " AND (project = ? OR project = 'global')"
                params.append(project)

        if type_:
            if type_ not in MEMORY_TYPES:
                raise ValidationError(f"Invalid type '{type_}'.")
            sql += " AND type = ?"
            params.append(type_)

        sql  += f" ORDER BY created_at DESC LIMIT {recent or limit}"
        rows  = conn.execute(sql, params).fetchall()

    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to list memories: {exc}") from exc

    label = "Memories"
    if AGENT.is_active:
        label += AGENT.banner()
    elif project:
        label += f" [{project}]"

    _print_memories(rows, label)
    return [dict(r) for r in rows]


def delete(conn: sqlite3.Connection, memory_id: int) -> None:
    """Delete a memory by ID."""
    try:
        row = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if not row:
            raise DatabaseError(f"No memory found with ID {memory_id}.")
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to delete memory: {exc}") from exc

    icon = "🗑️ " if _EMOJI_OK else "[DEL]"
    print(f"{icon} Deleted memory [ID: {memory_id}]")
    print(f"   {row['content'][:80]}")

    VAULT.on_delete(dict(row))


def update(
    conn:       sqlite3.Connection,
    memory_id:  int,
    content:    Optional[str] = None,
    importance: Optional[int] = None,
    tags:       Optional[str] = None,
) -> None:
    """Patch one or more fields of an existing memory."""
    try:
        row = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if not row:
            raise DatabaseError(f"No memory found with ID {memory_id}.")
    except sqlite3.Error as exc:
        raise DatabaseError(f"Database error: {exc}") from exc

    new_content    = validate_content(content if content is not None else row["content"])
    new_importance = validate_importance(importance if importance is not None else row["importance"])
    new_tags       = validate_tags(tags if tags is not None else row["tags"])

    try:
        conn.execute(
            "UPDATE memories SET content=?, importance=?, tags=?, updated_at=? "
            "WHERE id=?",
            (new_content, new_importance, new_tags, _now(), memory_id),
        )
        conn.commit()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to update memory: {exc}") from exc

    icon = "✏️ " if _EMOJI_OK else "[UPD]"
    print(f"{icon} Updated memory [ID: {memory_id}]")

    # Mirror to Obsidian vault
    VAULT.on_update({
        "id": memory_id, "type": row["type"], "content": new_content,
        "project": row["project"], "agent_id": row["agent_id"],
        "tags": new_tags, "importance": new_importance,
        "created_at": row["created_at"], "updated_at": _now(),
    })


def export_memories(
    conn:        sqlite3.Connection,
    project:     Optional[str] = None,
    output_file: Optional[str] = None,
) -> None:
    """Dump memories to JSON (stdout or file)."""
    sql    = "SELECT * FROM memories"
    params: list = []
    if project and project != "all":
        sql += " WHERE project = ? OR project = 'global'"
        params.append(project)
    sql += " ORDER BY type, created_at"

    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.Error as exc:
        raise DatabaseError(f"Export failed: {exc}") from exc

    data = {"exported_at": _now(), "total": len(rows), "memories": rows}

    if output_file:
        try:
            Path(output_file).write_text(
                json.dumps(data, ensure_ascii=False, indent=2)
            )
            print(f"{'✅' if _EMOJI_OK else '[OK]'} Exported {len(rows)} "
                  f"memories -> {output_file}")
        except OSError as exc:
            raise DatabaseError(f"Cannot write export file: {exc}") from exc
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def import_memories(
    conn:       sqlite3.Connection,
    input_file: str,
    overwrite:  bool = False,
) -> None:
    """Import memories from a JSON backup produced by export_memories."""
    try:
        data = json.loads(Path(input_file).read_text())
    except OSError as exc:
        raise DatabaseError(f"Cannot read import file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid JSON in import file: {exc}") from exc

    memories = data.get("memories", [])
    if not memories:
        warn = "⚠️ " if _EMOJI_OK else "[!]"
        print(f"{warn} No memories found in import file.")
        return

    imported = 0
    try:
        for m in memories:
            if "type" not in m or "content" not in m:
                logger.warning("Skipping invalid memory entry: %s", m)
                continue
            if m["type"] not in MEMORY_TYPES:
                logger.warning("Skipping unknown type '%s'.", m["type"])
                continue

            if overwrite:
                conn.execute(
                    """INSERT OR REPLACE INTO memories
                       (id, type, content, project, agent_id, tags, importance,
                        created_at, updated_at, access_count, last_accessed)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        m.get("id"),           m["type"],
                        m["content"],          m.get("project", "global"),
                        m.get("agent_id", ""), m.get("tags", ""),
                        m.get("importance", 3),
                        m.get("created_at", _now()),
                        m.get("updated_at", _now()),
                        m.get("access_count", 0), m.get("last_accessed"),
                    ),
                )
            else:
                conn.execute(
                    """INSERT OR IGNORE INTO memories
                       (type, content, project, agent_id, tags, importance,
                        created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        m["type"],             m["content"],
                        m.get("project", "global"),
                        m.get("agent_id", ""), m.get("tags", ""),
                        m.get("importance", 3),
                        m.get("created_at", _now()),
                        m.get("updated_at", _now()),
                    ),
                )
            imported += 1

        conn.commit()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Import failed: {exc}") from exc

    print(f"{'✅' if _EMOJI_OK else '[OK]'} Imported {imported} memories "
          f"from {input_file}")


def stats(conn: sqlite3.Connection) -> None:
    """Print a summary of the memory database."""
    try:
        total    = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        by_type  = conn.execute(
            "SELECT type, COUNT(*) n FROM memories GROUP BY type"
        ).fetchall()
        by_proj  = conn.execute(
            "SELECT project, COUNT(*) n FROM memories "
            "GROUP BY project ORDER BY n DESC LIMIT 10"
        ).fetchall()
        top_used = conn.execute(
            "SELECT id, content, access_count FROM memories "
            "ORDER BY access_count DESC LIMIT 5"
        ).fetchall()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to retrieve statistics: {exc}") from exc

    s_icon = "📊" if _EMOJI_OK else "[STATS]"
    f_icon = "📁" if _EMOJI_OK else "  >"
    print(f"\n{'='*55}")
    print(f"{s_icon}  Agent Memory - Statistics")
    print(f"  DB: {DB_PATH}")
    print(f"{'='*55}")
    print(f"  Total memories : {total}")
    print(f"\n  By type:")
    for r in by_type:
        print(f"    {_t(r['type'])} {r['type']:<12} {r['n']}")
    print(f"\n  Top projects:")
    for r in by_proj:
        print(f"    {f_icon} {r['project']:<20} {r['n']}")
    print(f"\n  Most accessed:")
    for r in top_used:
        snippet = r["content"][:50] + ("..." if len(r["content"]) > 50 else "")
        print(f"    [{r['id']:>3}] {snippet} (x{r['access_count']})")
    print(f"{'='*55}\n")


# ─── Context Generator ───────────────────────────────────────────────────────

def generate_context(conn: sqlite3.Connection, output: str = "CLAUDE.md") -> None:
    """Generate a CLAUDE.md file from the most important memories."""
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    lines = ["# Agent Memory Context", ""]
    lines.append("> Auto-generated by `memory generate-context`. "
                 "Do not edit manually.")
    lines.append("")

    if total == 0:
        lines.append("No memories stored yet.")
        Path(output).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{'✅' if _EMOJI_OK else '[OK]'} Generated {output} (empty)")
        return

    # Critical memories (importance 4-5)
    critical = conn.execute(
        "SELECT type, content, project, tags FROM memories "
        "WHERE importance >= 4 ORDER BY importance DESC, created_at DESC "
        "LIMIT 20"
    ).fetchall()
    if critical:
        lines.append("## Critical Context")
        lines.append("")
        for m in critical:
            icon = {"episodic": "📅", "semantic": "📚", "procedural": "⚙️"
                    }.get(m["type"], "•")
            tags = f" `{m['tags']}`" if m["tags"] else ""
            lines.append(f"- {icon} **[{m['project']}]** {m['content']}{tags}")
        lines.append("")

    # Recent episodic
    recent_ep = conn.execute(
        "SELECT content, created_at FROM memories "
        "WHERE type = 'episodic' ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    if recent_ep:
        lines.append("## Recent Decisions")
        lines.append("")
        for m in recent_ep:
            lines.append(f"- ({m['created_at'][:10]}) {m['content']}")
        lines.append("")

    # Key procedures
    procs = conn.execute(
        "SELECT content FROM memories WHERE type = 'procedural' "
        "AND importance >= 4 ORDER BY importance DESC LIMIT 10"
    ).fetchall()
    if procs:
        lines.append("## Key Procedures")
        lines.append("")
        for m in procs:
            lines.append(f"- {m['content']}")
        lines.append("")

    # Active projects
    projects = conn.execute(
        "SELECT project, COUNT(*) n FROM memories "
        "GROUP BY project ORDER BY n DESC LIMIT 10"
    ).fetchall()
    if projects:
        lines.append("## Active Projects")
        lines.append("")
        for p in projects:
            lines.append(f"- **{p['project']}**: {p['n']} memories")
        lines.append("")

    text = "\n".join(lines) + "\n"
    Path(output).write_text(text, encoding="utf-8")
    ok = "✅" if _EMOJI_OK else "[OK]"
    print(f"{ok} Generated {output} "
          f"({len(critical)} critical, {len(recent_ep)} recent, "
          f"{len(projects)} projects)")


# ─── Agent Commands ───────────────────────────────────────────────────────────

def who() -> None:
    """Print the current agent identity and memory scope."""
    ok   = "✅" if _EMOJI_OK else "[OK]"
    warn = "⚠️ " if _EMOJI_OK else "[!]"
    print(f"\n{'─'*50}")
    print("  Agent Memory - Current Context")
    print(f"{'─'*50}")
    if AGENT.is_active:
        role = "ORCHESTRATOR" if AGENT.is_orchestrator else "AGENT"
        print(f"  {ok} Mode        : multi-agent (isolation ON)")
        print(f"     Role        : {role}")
        print(f"     Name        : {AGENT.name}")
        print(f"     Write scope : {AGENT.namespace}")
        if AGENT.is_orchestrator:
            print("     Read scope  : ALL namespaces")
        else:
            print(f"     Read scope  : {AGENT.namespace} + global")
        print("\n  To change agent : export AGENT_NAME=<name>")
    else:
        print(f"  {warn} Mode        : single-agent (no isolation)")
        print("     AGENT_NAME not set - all memories are shared")
        print("\n  To enable isolation:")
        print("     export AGENT_NAME=health        # Linux / macOS / WSL")
        print('     $env:AGENT_NAME = "health"      # Windows PowerShell')
        print("     set AGENT_NAME=health           # Windows CMD")
    print(f"{'─'*50}")

    # Obsidian vault status
    if VAULT.is_active:
        v_ok = "✅" if _EMOJI_OK else "[OK]"
        print(f"\n  {v_ok} Obsidian    : CONNECTED")
        print(f"     Vault       : {VAULT.vault_path}")
        print(f"     Memories    : {VAULT.memory_dir}")
    else:
        v_off = "⚪" if _EMOJI_OK else "[ ]"
        print(f"\n  {v_off} Obsidian    : not connected")
        print("     To enable   : export OBSIDIAN_VAULT=/path/to/vault")

    print(f"{'─'*50}\n")


def list_agents(conn: sqlite3.Connection) -> None:
    """List every agent namespace that has at least one stored memory."""
    try:
        rows = conn.execute(
            """SELECT agent_id, COUNT(*) n, MAX(created_at) last_write
               FROM memories
               WHERE agent_id != ''
               GROUP BY agent_id
               ORDER BY n DESC"""
        ).fetchall()
        shared = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE agent_id = '' OR agent_id IS NULL"
        ).fetchone()[0]
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to list agents: {exc}") from exc

    a_icon = "🤖" if _EMOJI_OK else "*"
    g_icon = "🌐" if _EMOJI_OK else "G"
    print(f"\n{'─'*50}")
    print("  Agent Namespaces in DB")
    print(f"{'─'*50}")
    if not rows:
        print("  No agent-isolated memories yet.")
        print("  Set AGENT_NAME to start isolating.\n")
        return
    for r in rows:
        print(f"  {a_icon} {r['agent_id']:<20} {r['n']:>3} memories  "
              f"last={r['last_write'][:10]}")
    if shared:
        print(f"  {g_icon} global               {shared:>3} memories  "
              "(shared / untagged)")
    print(f"{'─'*50}\n")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="memory",
        description="Agent Memory - persistent memory for Claude Code agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  memory store --type episodic   --content "Chose FastAPI over Flask" --project api
  memory store --type semantic   --content "Max upload: 50 MB"        --project api --importance 5
  memory store --type procedural --content "Always run pytest before commit" --project global

  memory search --query "FastAPI"
  memory list   --project api --recent 10
  memory list   --type procedural
  memory update --id 3 --importance 5
  memory delete --id 7
  memory export --output backup.json
  memory import --input backup.json
  memory stats
  memory who
  memory agents

Multi-agent isolation (zero config):
  export AGENT_NAME=health          # Linux / macOS / WSL
  $env:AGENT_NAME = "health"        # Windows PowerShell
  set AGENT_NAME=health             # Windows CMD

Obsidian sync (optional):
  export OBSIDIAN_VAULT=/path/to/vault
  memory sync-to-vault              # SQLite -> Markdown
  memory sync-from-vault            # Markdown -> SQLite
        """,
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("store",  help="Save a new memory")
    s.add_argument("--type",       required=True, choices=MEMORY_TYPES)
    s.add_argument("--content",    required=True)
    s.add_argument("--project",    default="global")
    s.add_argument("--tags",       default="")
    s.add_argument("--importance", type=int, default=3, choices=range(1, 6))

    s = sub.add_parser("search", help="Full-text search memories")
    s.add_argument("--query",   required=True)
    s.add_argument("--project", default=None)
    s.add_argument("--type",    default=None, choices=MEMORY_TYPES)
    s.add_argument("--limit",   type=int, default=10)

    s = sub.add_parser("list",   help="List memories")
    s.add_argument("--project", default=None)
    s.add_argument("--type",    default=None, choices=MEMORY_TYPES)
    s.add_argument("--recent",  type=int, default=None)
    s.add_argument("--limit",   type=int, default=20)

    s = sub.add_parser("update", help="Update an existing memory")
    s.add_argument("--id",         type=int, required=True)
    s.add_argument("--content",    default=None)
    s.add_argument("--importance", type=int, default=None, choices=range(1, 6))
    s.add_argument("--tags",       default=None)

    s = sub.add_parser("delete", help="Delete a memory by ID")
    s.add_argument("--id", type=int, required=True)

    s = sub.add_parser("export", help="Export memories to JSON")
    s.add_argument("--project", default=None)
    s.add_argument("--output",  default=None)

    s = sub.add_parser("import", help="Import memories from a JSON backup")
    s.add_argument("--input",     required=True)
    s.add_argument("--overwrite", action="store_true",
                   help="Replace existing memories with matching IDs")

    sub.add_parser("stats",  help="Show database statistics")
    sub.add_parser("who",    help="Show current agent identity and scope")
    sub.add_parser("agents", help="List all agent namespaces in the database")

    # Obsidian sync (optional — requires OBSIDIAN_VAULT env var)
    sub.add_parser("sync-to-vault",
                   help="Export all memories as Markdown to Obsidian vault")
    sub.add_parser("sync-from-vault",
                   help="Import Markdown files from Obsidian vault into SQLite")

    s = sub.add_parser("generate-context",
                       help="Generate a CLAUDE.md file from key memories")
    s.add_argument("--output", default="CLAUDE.md",
                   help="Output path (default: CLAUDE.md)")

    return p


def main() -> int:
    """Entry point. Returns a POSIX exit code."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = build_parser()
    args   = parser.parse_args()

    try:
        conn = get_connection()
        init_db(conn)

        cmd = args.command
        if   cmd == "store":   store(conn, args.type, args.content, args.project, args.tags, args.importance)
        elif cmd == "search":  search(conn, args.query, args.project, args.type, args.limit)
        elif cmd == "list":    list_memories(conn, args.project, args.type, args.recent, args.limit)
        elif cmd == "update":  update(conn, args.id, args.content, args.importance, args.tags)
        elif cmd == "delete":  delete(conn, args.id)
        elif cmd == "export":  export_memories(conn, args.project, args.output)
        elif cmd == "import":  import_memories(conn, args.input, args.overwrite)
        elif cmd == "stats":   stats(conn)
        elif cmd == "who":     who()
        elif cmd == "agents":  list_agents(conn)
        elif cmd == "sync-to-vault":   VAULT.sync_to_vault(conn)
        elif cmd == "sync-from-vault": VAULT.sync_from_vault(conn)
        elif cmd == "generate-context": generate_context(conn, args.output)
        return 0

    except ValidationError as exc:
        print(f"{'❌' if _EMOJI_OK else '[ERR]'} Validation error: {exc}")
        return 1
    except DatabaseError as exc:
        print(f"{'❌' if _EMOJI_OK else '[ERR]'} Database error: {exc}")
        return 2
    except MemorySystemError as exc:
        print(f"{'❌' if _EMOJI_OK else '[ERR]'} Error: {exc}")
        return 3
    except KeyboardInterrupt:
        print(f"\n{'⚠️ ' if _EMOJI_OK else '[!]'} Operation cancelled.")
        return 130
    finally:
        if "conn" in locals():
            conn.close()


if __name__ == "__main__":
    sys.exit(main())

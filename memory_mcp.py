#!/usr/bin/env python3
"""
Agent Memory MCP Server
Exposes the Agent Memory System as MCP tools for Claude Code / Claude Desktop.

Usage:
  1. Install:  pip install mcp
  2. Add to ~/.claude/settings.json:
     {
       "mcpServers": {
         "agent-memory": {
           "command": "python",
           "args": ["/path/to/memory_mcp.py"]
         }
       }
     }
  3. Restart Claude Code / Claude Desktop.

Environment Variables (all optional):
  AGENT_MEMORY_DIR   - Override database location
  AGENT_NAME         - Enable agent isolation
  OBSIDIAN_VAULT     - Enable Obsidian sync
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager
from typing import Optional

from mcp.server.fastmcp import FastMCP, Context

import memory as mem


# ─── Lifespan: open DB once, close on shutdown ─────────────────────────────

@asynccontextmanager
async def app_lifespan():
    conn = mem.get_connection()
    mem.init_db(conn)
    yield {"conn": conn}
    conn.close()


mcp = FastMCP(
    "agent_memory_mcp",
    lifespan=app_lifespan,
    instructions="""You have a persistent memory system connected.

ON EVERY CONVERSATION START:
1. Call memory_brief to load your key context.
2. Use this context to personalize your responses.

DURING CONVERSATION:
- When you learn something important → memory_store
- When you need to recall something → memory_search
- When information changes → memory_update
- When asked "what do you remember" → memory_list

MEMORY TYPES:
- episodic: decisions, events, meetings ("Chose FastAPI over Flask")
- semantic: facts, configs, knowledge ("Max upload: 50MB")
- procedural: workflows, steps ("Always run pytest before commit")

IMPORTANCE GUIDE:
- 5: Critical decisions, credentials, architecture choices
- 4: Project conventions, key contacts, recurring patterns
- 3: General notes (default)
- 2: Minor observations
- 1: Temporary / low value
""",
)


# ─── Helper ─────────────────────────────────────────────────────────────────

def _conn(ctx) -> sqlite3.Connection:
    return ctx.request_context.lifespan_state["conn"]


def _mem_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id":           row["id"],
        "type":         row["type"],
        "content":      row["content"],
        "project":      row["project"],
        "tags":         row["tags"],
        "importance":   row["importance"],
        "created_at":   row["created_at"],
        "access_count": row["access_count"],
    }


# ─── Tools ──────────────────────────────────────────────────────────────────

@mcp.tool(
    name="memory_store",
    annotations={
        "title": "Store a new memory",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def memory_store(
    content: str,
    type: str = "semantic",
    project: str = "global",
    tags: str = "",
    importance: int = 3,
    ctx: Context = None,
) -> str:
    """Store a new memory in the agent memory database.

    Use this to persist facts, decisions, procedures, or any information
    that should be available across sessions.

    Args:
        content:    The memory text to store (max 50,000 chars).
        type:       Memory type — 'episodic' (events/decisions),
                    'semantic' (facts/knowledge), or 'procedural' (how-tos).
        project:    Project namespace (default 'global').
        tags:       Comma-separated tags for categorization.
        importance: Rating 1-5 (default 3).

    Returns:
        JSON with the new memory ID and details.
    """
    conn = _conn(ctx)
    mid = mem.store(conn, type, content, project, tags, importance)
    return json.dumps({
        "status": "stored",
        "id": mid,
        "type": type,
        "project": mem.AGENT.resolve_write_project(project),
        "importance": importance,
    })


@mcp.tool(
    name="memory_search",
    annotations={
        "title": "Search memories",
        "readOnlyHint": False,  # updates access_count
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def memory_search(
    query: str,
    project: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = 10,
    ctx: Context = None,
) -> str:
    """Full-text search across stored memories.

    Searches content and tags using SQLite FTS5 with ranked results.
    Results are automatically scoped to the current agent's namespace.

    Args:
        query:   Search terms (FTS5 syntax supported).
        project: Filter to a specific project (optional).
        type:    Filter by memory type (optional).
        limit:   Maximum results (1-100, default 10).

    Returns:
        JSON array of matching memories sorted by relevance.
    """
    conn = _conn(ctx)
    rows = mem.search(conn, query, project, type, limit)
    return json.dumps({"count": len(rows), "memories": rows}, default=str)


@mcp.tool(
    name="memory_list",
    annotations={
        "title": "List memories",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def memory_list(
    project: Optional[str] = None,
    type: Optional[str] = None,
    recent: Optional[int] = None,
    limit: int = 20,
    ctx: Context = None,
) -> str:
    """List memories with optional filters.

    Args:
        project: Filter to a specific project.
        type:    Filter by type (episodic/semantic/procedural).
        recent:  Limit to N most recent memories.
        limit:   Maximum results (default 20).

    Returns:
        JSON array of memories sorted by creation date (newest first).
    """
    conn = _conn(ctx)
    rows = mem.list_memories(conn, project, type, recent, limit)
    return json.dumps({"count": len(rows), "memories": rows}, default=str)


@mcp.tool(
    name="memory_update",
    annotations={
        "title": "Update an existing memory",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def memory_update(
    id: int,
    content: Optional[str] = None,
    importance: Optional[int] = None,
    tags: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """Update content, importance, or tags of an existing memory.

    Only the provided fields are changed — omitted fields keep their
    current values.

    Args:
        id:         Memory ID to update.
        content:    New content text (optional).
        importance: New importance 1-5 (optional).
        tags:       New comma-separated tags (optional).

    Returns:
        JSON confirmation with the updated memory ID.
    """
    conn = _conn(ctx)
    mem.update(conn, id, content, importance, tags)
    return json.dumps({"status": "updated", "id": id})


@mcp.tool(
    name="memory_delete",
    annotations={
        "title": "Delete a memory",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def memory_delete(id: int, ctx: Context = None) -> str:
    """Permanently delete a memory by ID.

    Args:
        id: Memory ID to delete.

    Returns:
        JSON confirmation.
    """
    conn = _conn(ctx)
    mem.delete(conn, id)
    return json.dumps({"status": "deleted", "id": id})


@mcp.tool(
    name="memory_stats",
    annotations={
        "title": "Memory system statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def memory_stats(ctx: Context = None) -> str:
    """Show statistics about the memory database.

    Returns total count, breakdown by type and project, and most accessed
    memories.

    Returns:
        JSON with total, by_type, by_project, and top_accessed fields.
    """
    conn = _conn(ctx)
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    by_type = [
        {"type": r["type"], "count": r["n"]}
        for r in conn.execute(
            "SELECT type, COUNT(*) n FROM memories GROUP BY type"
        ).fetchall()
    ]
    by_proj = [
        {"project": r["project"], "count": r["n"]}
        for r in conn.execute(
            "SELECT project, COUNT(*) n FROM memories "
            "GROUP BY project ORDER BY n DESC LIMIT 10"
        ).fetchall()
    ]
    return json.dumps({
        "total": total,
        "by_type": by_type,
        "by_project": by_proj,
        "agent": mem.AGENT.name or "(single-agent mode)",
        "obsidian": str(mem.VAULT.vault_path) if mem.VAULT.is_active else "not connected",
    })


@mcp.tool(
    name="memory_who",
    annotations={
        "title": "Current agent identity and scope",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def memory_who(ctx: Context = None) -> str:
    """Show the current agent identity, namespace, and Obsidian status.

    Returns:
        JSON with agent name, role, write/read scope, and Obsidian connection.
    """
    a = mem.AGENT
    info = {
        "mode": "multi-agent" if a.is_active else "single-agent",
        "agent": a.name,
        "role": "orchestrator" if a.is_orchestrator else "agent" if a.is_active else None,
        "write_scope": a.namespace if a.is_active else "all",
        "read_scope": "all" if (not a.is_active or a.is_orchestrator) else f"{a.namespace} + global",
        "obsidian": str(mem.VAULT.vault_path) if mem.VAULT.is_active else None,
    }
    return json.dumps(info)


@mcp.tool(
    name="memory_export",
    annotations={
        "title": "Export memories to JSON",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def memory_export(
    project: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """Export all memories (or a project's memories) as JSON.

    Args:
        project: Filter to a specific project (optional).

    Returns:
        JSON object with exported_at, total, and memories array.
    """
    conn = _conn(ctx)
    sql = "SELECT * FROM memories"
    params: list = []
    if project and project != "all":
        sql += " WHERE project = ? OR project = 'global'"
        params.append(project)
    sql += " ORDER BY type, created_at"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    return json.dumps({
        "exported_at": mem._now(),
        "total": len(rows),
        "memories": rows,
    }, default=str)


@mcp.tool(
    name="memory_sync_to_vault",
    annotations={
        "title": "Sync memories to Obsidian vault",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def memory_sync_to_vault(ctx: Context = None) -> str:
    """Export all memories as Markdown files to the Obsidian vault.

    Requires OBSIDIAN_VAULT environment variable to be set.

    Returns:
        JSON with sync count and vault path.
    """
    conn = _conn(ctx)
    count = mem.VAULT.sync_to_vault(conn)
    return json.dumps({
        "status": "synced",
        "count": count,
        "vault": str(mem.VAULT.memory_dir),
    })


@mcp.tool(
    name="memory_sync_from_vault",
    annotations={
        "title": "Import from Obsidian vault",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def memory_sync_from_vault(ctx: Context = None) -> str:
    """Import new Markdown files from Obsidian vault into the database.

    Only imports files with valid YAML front-matter that don't already
    exist in the database.  Requires OBSIDIAN_VAULT env var.

    Returns:
        JSON with import count.
    """
    conn = _conn(ctx)
    count = mem.VAULT.sync_from_vault(conn)
    return json.dumps({
        "status": "imported",
        "count": count,
        "vault": str(mem.VAULT.memory_dir),
    })


# ─── Context Brief (auto-load on connect) ──────────────────────────────────

def _build_brief(conn: sqlite3.Connection) -> dict:
    """Build a structured context brief from the most important memories."""
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    if total == 0:
        return {
            "status": "empty",
            "message": "No memories stored yet. Start a conversation and "
                       "use memory_store to build your context.",
        }

    # High-importance memories (4-5)
    critical = [
        dict(r) for r in conn.execute(
            "SELECT id, type, content, project, tags, importance "
            "FROM memories WHERE importance >= 4 "
            "ORDER BY importance DESC, created_at DESC LIMIT 20"
        ).fetchall()
    ]

    # Most frequently accessed
    frequent = [
        dict(r) for r in conn.execute(
            "SELECT id, type, content, project, importance, access_count "
            "FROM memories WHERE access_count > 0 "
            "ORDER BY access_count DESC LIMIT 10"
        ).fetchall()
    ]

    # Recent memories (last 10)
    recent = [
        dict(r) for r in conn.execute(
            "SELECT id, type, content, project, tags, importance, created_at "
            "FROM memories ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
    ]

    # Projects overview
    projects = [
        {"project": r["project"], "count": r["n"]}
        for r in conn.execute(
            "SELECT project, COUNT(*) n FROM memories "
            "GROUP BY project ORDER BY n DESC LIMIT 10"
        ).fetchall()
    ]

    return {
        "status": "loaded",
        "total_memories": total,
        "agent": mem.AGENT.name or "(single-agent)",
        "critical_memories": critical,
        "most_accessed": frequent,
        "recent_memories": recent,
        "projects": projects,
    }


@mcp.tool(
    name="memory_brief",
    annotations={
        "title": "Load context brief (call this first!)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def memory_brief(ctx: Context = None) -> str:
    """Load a context brief of your most important memories.

    CALL THIS AT THE START OF EVERY CONVERSATION to restore your context.
    Returns critical memories (importance 4-5), most accessed, and recent
    memories so you can pick up where you left off.

    Returns:
        JSON with critical_memories, most_accessed, recent_memories,
        and projects overview.
    """
    conn = _conn(ctx)
    brief = _build_brief(conn)
    return json.dumps(brief, default=str)


@mcp.resource("memory://brief")
def resource_brief() -> str:
    """Auto-loaded context brief with key memories.

    This resource provides the same data as memory_brief but can be
    read automatically by MCP clients on connection.
    """
    conn = mem.get_connection()
    mem.init_db(conn)
    try:
        brief = _build_brief(conn)
        return json.dumps(brief, default=str, indent=2)
    finally:
        conn.close()


# ─── CLAUDE.md Generator ────────────────────────────────────────────────────

def generate_claude_md(conn: sqlite3.Connection, output: Optional[str] = None) -> str:
    """Generate a CLAUDE.md file from the most important memories.

    This creates a context file that Claude Code reads automatically at
    the start of every session — no tool call needed.
    """
    brief = _build_brief(conn)

    lines = ["# Agent Memory Context", ""]
    lines.append("> Auto-generated by `memory generate-context`. "
                 "Do not edit manually — regenerate after changes.")
    lines.append("")

    if brief.get("status") == "empty":
        lines.append("No memories stored yet.")
        text = "\n".join(lines) + "\n"
        if output:
            from pathlib import Path
            Path(output).write_text(text, encoding="utf-8")
        return text

    # Critical memories
    critical = brief.get("critical_memories", [])
    if critical:
        lines.append("## Critical Context")
        lines.append("")
        for m in critical:
            icon = {"episodic": "📅", "semantic": "📚", "procedural": "⚙️"}.get(
                m["type"], "•"
            )
            tags = f" `{m['tags']}`" if m.get("tags") else ""
            lines.append(f"- {icon} **[{m['project']}]** {m['content']}{tags}")
        lines.append("")

    # Recent decisions
    recent = brief.get("recent_memories", [])
    recent_ep = [m for m in recent if m["type"] == "episodic"]
    if recent_ep:
        lines.append("## Recent Decisions")
        lines.append("")
        for m in recent_ep[:5]:
            date = m.get("created_at", "")[:10]
            lines.append(f"- ({date}) {m['content']}")
        lines.append("")

    # Procedures
    procs = [m for m in critical if m["type"] == "procedural"]
    if procs:
        lines.append("## Key Procedures")
        lines.append("")
        for m in procs:
            lines.append(f"- {m['content']}")
        lines.append("")

    # Active projects
    projects = brief.get("projects", [])
    if projects:
        lines.append("## Active Projects")
        lines.append("")
        for p in projects:
            lines.append(f"- **{p['project']}**: {p['count']} memories")
        lines.append("")

    text = "\n".join(lines) + "\n"

    if output:
        from pathlib import Path
        Path(output).write_text(text, encoding="utf-8")
        ok = "✅" if mem._EMOJI_OK else "[OK]"
        print(f"{ok} Generated {output} ({len(critical)} critical, "
              f"{len(recent)} recent, {len(projects)} projects)")

    return text


@mcp.tool(
    name="memory_generate_context",
    annotations={
        "title": "Generate CLAUDE.md context file",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def memory_generate_context(
    output: str = "CLAUDE.md",
    ctx: Context = None,
) -> str:
    """Generate a CLAUDE.md file from your most important memories.

    This creates a Markdown file that Claude Code auto-reads at session start.
    Place it in your project root for automatic context injection.

    Args:
        output: Output file path (default: CLAUDE.md in current directory).

    Returns:
        JSON with status and file path.
    """
    conn = _conn(ctx)
    generate_claude_md(conn, output)
    return json.dumps({"status": "generated", "file": output})


# ─── Entry Point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()

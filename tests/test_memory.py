#!/usr/bin/env python3
"""
Unit Tests for Agent Memory System

Run with: python -m pytest tests/test_memory.py -v
"""

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the module under test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import memory


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test_memory.db"
    
    # Patch the DB_PATH
    with patch.object(memory, 'DB_PATH', db_path):
        with patch.object(memory, 'DB_DIR', tmp_path):
            conn = memory.get_connection()
            memory.init_db(conn)
            yield conn
    
    # Cleanup
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def clean_agent_context():
    """Reset agent context before and after each test."""
    # Save original
    original = memory.AGENT
    # Reset to inactive state
    memory.AGENT = memory.AgentContext.from_env()
    yield
    # Restore
    memory.AGENT = original


# ─── Validation Tests ─────────────────────────────────────────────────────────

class TestContentValidation:
    """Tests for content validation."""
    
    def test_valid_content(self):
        """Valid content should pass validation."""
        result = memory.validate_content("This is valid content")
        assert result == "This is valid content"
    
    def test_content_with_whitespace(self):
        """Content should be stripped of leading/trailing whitespace."""
        result = memory.validate_content("  Content with spaces  ")
        assert result == "Content with spaces"
    
    def test_empty_content_raises_error(self):
        """Empty content should raise ValidationError."""
        with pytest.raises(memory.ValidationError, match="cannot be empty"):
            memory.validate_content("")
    
    def test_whitespace_only_content_raises_error(self):
        """Whitespace-only content should raise ValidationError."""
        with pytest.raises(memory.ValidationError, match="cannot be empty"):
            memory.validate_content("   ")
    
    def test_too_long_content_raises_error(self):
        """Content exceeding MAX_CONTENT_LENGTH should raise ValidationError."""
        long_content = "x" * (memory.MAX_CONTENT_LENGTH + 1)
        with pytest.raises(memory.ValidationError, match="too long"):
            memory.validate_content(long_content)
    
    def test_max_length_content_accepted(self):
        """Content at exactly MAX_CONTENT_LENGTH should be accepted."""
        max_content = "x" * memory.MAX_CONTENT_LENGTH
        result = memory.validate_content(max_content)
        assert len(result) == memory.MAX_CONTENT_LENGTH


class TestTagsValidation:
    """Tests for tags validation."""
    
    def test_valid_tags(self):
        """Valid tags should pass validation."""
        result = memory.validate_tags("tag1, tag2, tag3")
        assert result == "tag1, tag2, tag3"
    
    def test_empty_tags(self):
        """Empty tags should be accepted."""
        result = memory.validate_tags("")
        assert result == ""
    
    def test_tags_with_special_chars_raises_error(self):
        """Tags with invalid characters should raise ValidationError."""
        with pytest.raises(memory.ValidationError, match="may only contain"):
            memory.validate_tags("tag1; DROP TABLE--")
    
    def test_too_long_tags_raises_error(self):
        """Tags exceeding MAX_TAGS_LENGTH should raise ValidationError."""
        long_tags = "tag, " * (memory.MAX_TAGS_LENGTH // 5 + 1)
        with pytest.raises(memory.ValidationError, match="too long"):
            memory.validate_tags(long_tags)


class TestProjectValidation:
    """Tests for project name validation."""
    
    def test_valid_project(self):
        """Valid project names should pass validation."""
        result = memory.validate_project("my-project")
        assert result == "my-project"
    
    def test_project_normalized_to_lowercase(self):
        """Project names should be normalized to lowercase."""
        result = memory.validate_project("MyProject")
        assert result == "myproject"
    
    def test_empty_project_defaults_to_global(self):
        """Empty project should default to 'global'."""
        result = memory.validate_project("")
        assert result == "global"
    
    def test_project_with_dots_allowed(self):
        """Project names with dots should be allowed."""
        result = memory.validate_project("agent.health")
        assert result == "agent.health"
    
    def test_project_with_invalid_chars_raises_error(self):
        """Project names with invalid characters should raise error."""
        with pytest.raises(memory.ValidationError):
            memory.validate_project("my project!")


class TestImportanceValidation:
    """Tests for importance validation."""
    
    def test_valid_importance(self):
        """Valid importance values should pass validation."""
        for i in range(1, 6):
            result = memory.validate_importance(i)
            assert result == i
    
    def test_importance_too_low_raises_error(self):
        """Importance below 1 should raise error."""
        with pytest.raises(memory.ValidationError):
            memory.validate_importance(0)
    
    def test_importance_too_high_raises_error(self):
        """Importance above 5 should raise error."""
        with pytest.raises(memory.ValidationError):
            memory.validate_importance(6)


# ─── Database Tests ───────────────────────────────────────────────────────────

class TestDatabaseOperations:
    """Tests for database operations."""
    
    def test_init_db_creates_tables(self, temp_db):
        """init_db should create all required tables."""
        cursor = temp_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor.fetchall()}
        
        assert "memories" in tables
        assert "memories_fts" in tables
        assert "_migrations" in tables
    
    def test_store_memory(self, temp_db):
        """store should insert a memory and return its ID."""
        mid = memory.store(
            temp_db,
            type_="episodic",
            content="Test memory content",
            project="test-project",
            tags="test, unit",
            importance=4
        )
        
        assert isinstance(mid, int)
        assert mid > 0
        
        # Verify the memory was stored
        row = temp_db.execute(
            "SELECT * FROM memories WHERE id = ?", (mid,)
        ).fetchone()
        
        assert row is not None
        assert row["content"] == "Test memory content"
        assert row["type"] == "episodic"
        assert row["project"] == "test-project"
    
    def test_store_invalid_type_raises_error(self, temp_db):
        """store with invalid type should raise ValidationError."""
        with pytest.raises(memory.ValidationError, match="Invalid type"):
            memory.store(temp_db, type_="invalid", content="Test")
    
    def test_search_memories(self, temp_db):
        """search should find memories matching the query."""
        memory.store(temp_db, "semantic", "Python is a programming language")
        memory.store(temp_db, "semantic", "JavaScript is also a programming language")
        memory.store(temp_db, "episodic", "I had lunch today")
        
        results = memory.search(temp_db, "programming")
        
        assert len(results) == 2
    
    def test_list_memories(self, temp_db):
        """list_memories should return all memories."""
        memory.store(temp_db, "episodic", "Memory 1")
        memory.store(temp_db, "semantic", "Memory 2")
        
        results = memory.list_memories(temp_db)
        
        assert len(results) == 2
    
    def test_list_memories_with_type_filter(self, temp_db):
        """list_memories should filter by type."""
        memory.store(temp_db, "episodic", "Memory 1")
        memory.store(temp_db, "semantic", "Memory 2")
        
        results = memory.list_memories(temp_db, type_="episodic")
        
        assert len(results) == 1
        assert results[0]["type"] == "episodic"
    
    def test_update_memory(self, temp_db):
        """update should modify an existing memory."""
        mid = memory.store(temp_db, "episodic", "Original content")
        
        memory.update(temp_db, mid, content="Updated content", importance=5)
        
        row = temp_db.execute(
            "SELECT * FROM memories WHERE id = ?", (mid,)
        ).fetchone()
        
        assert row["content"] == "Updated content"
        assert row["importance"] == 5
    
    def test_update_nonexistent_memory_raises_error(self, temp_db):
        """update on nonexistent memory should raise error."""
        with pytest.raises(memory.DatabaseError, match="No memory found"):
            memory.update(temp_db, 99999, content="Test")
    
    def test_delete_memory(self, temp_db):
        """delete should remove a memory."""
        mid = memory.store(temp_db, "episodic", "To be deleted")
        
        memory.delete(temp_db, mid)
        
        row = temp_db.execute(
            "SELECT * FROM memories WHERE id = ?", (mid,)
        ).fetchone()
        
        assert row is None
    
    def test_delete_nonexistent_memory_raises_error(self, temp_db):
        """delete on nonexistent memory should raise error."""
        with pytest.raises(memory.DatabaseError, match="No memory found"):
            memory.delete(temp_db, 99999)
    
    def test_export_memories(self, temp_db, tmp_path):
        """export_memories should write JSON file."""
        memory.store(temp_db, "episodic", "Memory 1")
        memory.store(temp_db, "semantic", "Memory 2")
        
        output_file = tmp_path / "export.json"
        memory.export_memories(temp_db, output_file=str(output_file))
        
        assert output_file.exists()
        
        data = json.loads(output_file.read_text())
        assert data["total"] == 2
        assert len(data["memories"]) == 2
    
    def test_import_memories(self, temp_db, tmp_path):
        """import_memories should read JSON file and insert memories."""
        # Create import file
        import_data = {
            "memories": [
                {
                    "type": "episodic",
                    "content": "Imported memory 1",
                    "project": "global",
                    "tags": "imported",
                    "importance": 3
                },
                {
                    "type": "semantic",
                    "content": "Imported memory 2",
                    "project": "global",
                    "tags": "imported",
                    "importance": 4
                }
            ]
        }
        
        import_file = tmp_path / "import.json"
        import_file.write_text(json.dumps(import_data))
        
        memory.import_memories(temp_db, str(import_file))
        
        # Verify memories were imported
        count = temp_db.execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone()[0]
        
        assert count == 2


# ─── Agent Context Tests ──────────────────────────────────────────────────────

class TestAgentContext:
    """Tests for agent context and isolation."""
    
    def test_inactive_agent_context(self, clean_agent_context):
        """AgentContext should be inactive when AGENT_NAME is not set."""
        ctx = memory.AgentContext.from_env()
        
        assert ctx.is_active is False
        assert ctx.name is None
        assert ctx.namespace is None
        assert ctx.is_orchestrator is False
    
    def test_active_agent_context(self, clean_agent_context):
        """AgentContext should be active when AGENT_NAME is set."""
        with patch.dict(os.environ, {"AGENT_NAME": "health"}):
            ctx = memory.AgentContext.from_env()
            
            assert ctx.is_active is True
            assert ctx.name == "health"
            assert ctx.namespace == "agent.health"
            assert ctx.is_orchestrator is False
    
    def test_orchestrator_context(self, clean_agent_context):
        """AgentContext should recognize orchestrator role."""
        with patch.dict(os.environ, {"AGENT_NAME": "orchestrator"}):
            ctx = memory.AgentContext.from_env()
            
            assert ctx.is_orchestrator is True
    
    def test_agent_name_sanitization(self, clean_agent_context):
        """AgentContext should sanitize agent name."""
        with patch.dict(os.environ, {"AGENT_NAME": "Health-Agent@123!"}):
            ctx = memory.AgentContext.from_env()
            
            # Hyphens and underscores are allowed, special chars removed
            assert ctx.name == "health-agent123"
    
    def test_resolve_write_project_inactive(self, clean_agent_context):
        """Inactive agent should pass through project unchanged."""
        ctx = memory.AgentContext.from_env()
        
        result = ctx.resolve_write_project("my-project")
        assert result == "my-project"
    
    def test_resolve_write_project_regular_agent(self, clean_agent_context):
        """Regular agent should be redirected to own namespace."""
        with patch.dict(os.environ, {"AGENT_NAME": "health"}):
            ctx = memory.AgentContext.from_env()
            
            # Requesting global should redirect to own namespace
            result = ctx.resolve_write_project("global")
            assert result == "agent.health"
            
            # Requesting other namespace should redirect to own
            result = ctx.resolve_write_project("agent.other")
            assert result == "agent.health"
            
            # Requesting own namespace should be allowed
            result = ctx.resolve_write_project("agent.health")
            assert result == "agent.health"
    
    def test_resolve_write_project_orchestrator(self, clean_agent_context):
        """Orchestrator should be allowed to write anywhere."""
        with patch.dict(os.environ, {"AGENT_NAME": "orchestrator"}):
            ctx = memory.AgentContext.from_env()
            
            result = ctx.resolve_write_project("any-project")
            assert result == "any-project"
            
            result = ctx.resolve_write_project("global")
            assert result == "global"
    
    def test_read_projects_inactive(self, clean_agent_context):
        """Inactive agent should read all projects."""
        ctx = memory.AgentContext.from_env()
        
        result = ctx.read_projects()
        assert result is None  # None means no restriction
    
    def test_read_projects_regular_agent(self, clean_agent_context):
        """Regular agent should only read own namespace and global."""
        with patch.dict(os.environ, {"AGENT_NAME": "health"}):
            ctx = memory.AgentContext.from_env()
            
            result = ctx.read_projects()
            assert result == ["agent.health", "global"]
    
    def test_read_projects_orchestrator(self, clean_agent_context):
        """Orchestrator should read all projects."""
        with patch.dict(os.environ, {"AGENT_NAME": "orchestrator"}):
            ctx = memory.AgentContext.from_env()
            
            result = ctx.read_projects()
            assert result is None  # None means no restriction

    def test_strict_mode_raises_isolation_error(self, clean_agent_context):
        """strict=True should raise IsolationError on cross-namespace writes."""
        with patch.dict(os.environ, {"AGENT_NAME": "health"}):
            ctx = memory.AgentContext.from_env()

            with pytest.raises(memory.IsolationError, match="cannot write"):
                ctx.resolve_write_project("agent.other", strict=True)

            with pytest.raises(memory.IsolationError, match="cannot write"):
                ctx.resolve_write_project("global", strict=True)

    def test_strict_mode_allows_own_namespace(self, clean_agent_context):
        """strict=True should still allow writes to the agent's own namespace."""
        with patch.dict(os.environ, {"AGENT_NAME": "health"}):
            ctx = memory.AgentContext.from_env()

            result = ctx.resolve_write_project("agent.health", strict=True)
            assert result == "agent.health"


# ─── Integration Tests ────────────────────────────────────────────────────────

class TestIntegration:
    """Integration tests for the full system."""
    
    def test_store_and_search_flow(self, temp_db):
        """Test the complete store and search flow."""
        # Store several memories
        memory.store(temp_db, "semantic", "FastAPI is better than Flask for async")
        memory.store(temp_db, "semantic", "Django is good for monolithic apps")
        memory.store(temp_db, "episodic", "I chose FastAPI for the new project")
        
        # Search should find relevant memories
        results = memory.search(temp_db, "FastAPI")
        assert len(results) == 2
        
        # Content should contain the search term
        for r in results:
            assert "FastAPI" in r["content"]
    
    def test_access_count_increments(self, temp_db):
        """Access count should increment on search."""
        mid = memory.store(temp_db, "semantic", "Test memory for access count")
        
        # Initial access count
        row = temp_db.execute(
            "SELECT access_count FROM memories WHERE id = ?", (mid,)
        ).fetchone()
        assert row["access_count"] == 0
        
        # Search for it
        memory.search(temp_db, "access count")
        
        # Access count should have incremented
        row = temp_db.execute(
            "SELECT access_count FROM memories WHERE id = ?", (mid,)
        ).fetchone()
        assert row["access_count"] == 1
    
    def test_agent_isolation_in_store(self, temp_db, clean_agent_context):
        """Memory should be isolated to agent namespace."""
        with patch.dict(os.environ, {"AGENT_NAME": "health"}):
            # Re-initialize agent context
            memory.AGENT = memory.AgentContext.from_env()
            
            mid = memory.store(
                temp_db, "semantic", "Health agent memory",
                project="global"  # Request global, should be redirected
            )
            
            row = temp_db.execute(
                "SELECT * FROM memories WHERE id = ?", (mid,)
            ).fetchone()
            
            # Should be in agent's namespace
            assert row["project"] == "agent.health"
            assert row["agent_id"] == "health"


# ─── Edge Case Tests ──────────────────────────────────────────────────────────

class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""
    
    def test_search_with_quotes(self, temp_db):
        """Search query with quotes should work."""
        memory.store(temp_db, "semantic", "He said 'hello world' to me")
        
        # Should not crash
        results = memory.search(temp_db, "hello world")
        assert len(results) == 1
    
    def test_unicode_content(self, temp_db):
        """Unicode content should be handled correctly."""
        mid = memory.store(temp_db, "semantic", "مرحبا بالعالم 世界你好")
        
        row = temp_db.execute(
            "SELECT content FROM memories WHERE id = ?", (mid,)
        ).fetchone()
        
        assert row["content"] == "مرحبا بالعالم 世界你好"
    
    def test_export_import_roundtrip(self, temp_db, tmp_path):
        """Export and import should preserve all data."""
        # Store some memories
        memory.store(temp_db, "episodic", "Memory 1", tags="tag1", importance=5)
        memory.store(temp_db, "semantic", "Memory 2", tags="tag2", importance=3)
        memory.store(temp_db, "procedural", "Memory 3", tags="tag3", importance=1)
        
        # Export
        export_file = tmp_path / "export.json"
        memory.export_memories(temp_db, output_file=str(export_file))
        
        # Clear the database
        temp_db.execute("DELETE FROM memories")
        temp_db.commit()
        
        # Import
        memory.import_memories(temp_db, str(export_file))
        
        # Verify
        count = temp_db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        assert count == 3


# ─── Obsidian Bridge Tests ───────────────────────────────────────────────────

class TestObsidianBridge:
    """Tests for the optional Obsidian vault integration."""

    def test_inactive_bridge_by_default(self):
        """Bridge should be inactive when OBSIDIAN_VAULT is not set."""
        bridge = memory.ObsidianBridge.from_env()
        assert bridge.is_active is False
        assert bridge.vault_path is None

    def test_active_bridge_from_env(self, tmp_path):
        """Bridge should activate when OBSIDIAN_VAULT is set."""
        with patch.dict(os.environ, {"OBSIDIAN_VAULT": str(tmp_path)}):
            bridge = memory.ObsidianBridge.from_env()
            assert bridge.is_active is True
            assert bridge.vault_path == tmp_path
            assert bridge.memory_dir == tmp_path / "memories"

    def test_on_store_creates_markdown(self, tmp_path):
        """on_store should create a Markdown file with front-matter."""
        bridge = memory.ObsidianBridge(
            vault_path=tmp_path, is_active=True,
            memory_dir=tmp_path / "memories",
        )
        mem = {
            "id": 1, "type": "semantic", "content": "Max upload 50MB",
            "project": "api", "agent_id": "", "tags": "config, limits",
            "importance": 5, "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        bridge.on_store(mem)

        # File should exist under memories/api/
        files = list((tmp_path / "memories" / "api").glob("*.md"))
        assert len(files) == 1

        text = files[0].read_text(encoding="utf-8")
        assert "---" in text
        assert "type: semantic" in text
        assert "importance: 5" in text
        assert "Max upload 50MB" in text
        assert "#config" in text
        assert "#limits" in text

    def test_on_delete_removes_file(self, tmp_path):
        """on_delete should remove the Markdown file."""
        bridge = memory.ObsidianBridge(
            vault_path=tmp_path, is_active=True,
            memory_dir=tmp_path / "memories",
        )
        mem = {
            "id": 7, "type": "episodic", "content": "Delete me",
            "project": "global", "agent_id": "", "tags": "",
            "importance": 3, "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        bridge.on_store(mem)
        assert len(list((tmp_path / "memories" / "global").glob("*.md"))) == 1

        bridge.on_delete(mem)
        assert len(list((tmp_path / "memories" / "global").glob("*.md"))) == 0

    def test_on_update_rewrites_file(self, tmp_path):
        """on_update should rewrite the Markdown with new content."""
        bridge = memory.ObsidianBridge(
            vault_path=tmp_path, is_active=True,
            memory_dir=tmp_path / "memories",
        )
        mem = {
            "id": 3, "type": "semantic", "content": "Old content",
            "project": "global", "agent_id": "", "tags": "",
            "importance": 3, "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        bridge.on_store(mem)

        mem["content"] = "New updated content"
        mem["importance"] = 5
        bridge.on_update(mem)

        files = list((tmp_path / "memories" / "global").glob("*.md"))
        assert len(files) == 1
        text = files[0].read_text(encoding="utf-8")
        assert "New updated content" in text
        assert "importance: 5" in text

    def test_inactive_bridge_is_noop(self):
        """Inactive bridge should do nothing on store/update/delete."""
        bridge = memory.ObsidianBridge()  # inactive
        mem = {"id": 1, "type": "semantic", "content": "test",
               "project": "global", "agent_id": "", "tags": "",
               "importance": 3, "created_at": "", "updated_at": ""}
        # These should all silently succeed
        bridge.on_store(mem)
        bridge.on_update(mem)
        bridge.on_delete(mem)

    def test_sync_to_vault(self, temp_db, tmp_path):
        """sync_to_vault should export all memories as Markdown."""
        bridge = memory.ObsidianBridge(
            vault_path=tmp_path, is_active=True,
            memory_dir=tmp_path / "memories",
        )
        memory.store(temp_db, "semantic", "Fact one", project="api")
        memory.store(temp_db, "episodic", "Event two", project="api")
        memory.store(temp_db, "procedural", "Step three", project="global")

        count = bridge.sync_to_vault(temp_db)
        assert count == 3

        all_md = list((tmp_path / "memories").rglob("*.md"))
        assert len(all_md) == 3

    def test_sync_from_vault(self, temp_db, tmp_path):
        """sync_from_vault should import Markdown files into SQLite."""
        bridge = memory.ObsidianBridge(
            vault_path=tmp_path, is_active=True,
            memory_dir=tmp_path / "memories",
        )
        # Create a Markdown file manually
        folder = tmp_path / "memories" / "test-proj"
        folder.mkdir(parents=True)
        md = folder / "0099_manual-note.md"
        md.write_text(
            "---\nid: 99\ntype: semantic\nproject: test-proj\n"
            "importance: 4\ncreated: 2026-01-01T00:00:00Z\n"
            "updated: 2026-01-01T00:00:00Z\ntags: manual\n---\n\n"
            "This was written in Obsidian\n",
            encoding="utf-8",
        )

        count = bridge.sync_from_vault(temp_db)
        assert count == 1

        rows = temp_db.execute("SELECT * FROM memories").fetchall()
        assert len(rows) == 1
        assert rows[0]["content"] == "This was written in Obsidian"
        assert rows[0]["project"] == "test-proj"

    def test_parse_frontmatter(self):
        """_parse_frontmatter should split YAML and body."""
        text = "---\ntype: semantic\nproject: api\n---\n\nBody text here\n"
        meta, body = memory.ObsidianBridge._parse_frontmatter(text)
        assert meta["type"] == "semantic"
        assert meta["project"] == "api"
        assert "Body text here" in body

    def test_parse_frontmatter_no_yaml(self):
        """Files without front-matter should return empty dict."""
        text = "Just plain text\n"
        meta, body = memory.ObsidianBridge._parse_frontmatter(text)
        assert meta == {}
        assert body == text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

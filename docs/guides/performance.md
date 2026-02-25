# Performance Guide: Avoiding VectorStore Initialization Hangs

## Problem

AI assistants (Zencoder, Claude, etc.) can experience 30–60 second hangs when calling lightweight tools like `read_memory()` in parallel with search tools.

## Root Cause

**Lazy initialization of VectorStore** during parallel tool calls:

1. AI calls multiple tools in parallel (e.g., `read_memory()` + `search_codebase()`)
2. Each tool calls `get_context()` which creates `AppContext`
3. If any tool triggers vector search, it calls `VectorStoreManager.initialize()`
4. **Initialization loads SentenceTransformer model (30–60 seconds)**
5. Other parallel tools block or timeout waiting for the lock

## Solution

Memory and lightweight tools bypass `get_context()` entirely and work directly with files or a minimal `MemoryManager` instance.

### Tools that are always fast (no VectorStore)

**Memory operations:**
- `read_memory(max_lines)` — direct file read
- `get_project_memory()` — direct file read
- `update_memory(content, section)` — lightweight MemoryManager
- `clear_memory(keep_template)` — lightweight MemoryManager
- `delete_memory_section(section_name)` — lightweight MemoryManager
- `save_memory_version(description)` — lightweight MemoryManager
- `list_memory_versions()` — lightweight MemoryManager
- `restore_memory_version(timestamp)` — lightweight MemoryManager

**Navigation / overview:**
- `get_project_overview()`, `explore_directory()`, `get_file_summary()`
- `get_index_stats()` — checks DB existence before creating context

### Tools that initialize VectorStore (slow on first call)

- `index_codebase()` — always slow (processes all files)
- `search_codebase()` — loads embedding model on first use (~30–60s)
- `search_codebase_advanced()` — same as above
- `index_changed_files()` — incremental but still initializes model

## Implementation Details

### read_memory() — direct file access

```python
# BEFORE (could block on VectorStore init)
def read_memory(max_lines: int | None = 100) -> str:
    ctx = get_context()
    return ctx.memory_manager.read(max_lines=max_lines)

# AFTER (instant)
def read_memory(max_lines: int | None = 100) -> str:
    content = config.MEMORY_FILE.read_text()
    # ... pagination logic ...
```

### Memory operations — lightweight MemoryManager

```python
# BEFORE
def update_memory(content: str, section: str = "Recent Decisions") -> str:
    ctx = get_context()
    return ctx.memory_manager.update(content, section)

# AFTER
def update_memory(content: str, section: str = "Recent Decisions") -> str:
    from memory_manager import MemoryManager
    mm = MemoryManager()
    return mm.update(content, section)
```

### get_index_stats() — pre-check before context creation

```python
# BEFORE
def get_index_stats() -> str:
    ctx = get_context()
    if not ctx.vector_store._initialized:
        return "Vector store not initialized."

# AFTER
def get_index_stats() -> str:
    vector_db_path = config.VECTOR_STORE_DIR / "chroma.sqlite3"
    if not vector_db_path.exists():
        return "Vector store not initialized. Run index_codebase() first."
    ctx = get_context()
    ...
```

## Performance Impact

| Operation | Before | After |
|-----------|--------|-------|
| `read_memory()` | 0–60s (if parallel) | <10ms |
| `update_memory()` | 0–60s (if parallel) | <50ms |
| `get_index_stats()` | 0–60s (if parallel) | <10ms |

## Best Practices for AI Assistants

1. **Start sessions with fast tools** — `get_project_overview()`, `read_memory()`
2. **Avoid mixing memory + search in the same parallel batch** — call search tools separately
3. **Expect the first `search_codebase()` call to take 30–60s** — the model loads once and is reused

## Verification

```bash
python -m py_compile mcp_server.py
pytest tests/test_mcp_tools.py -k memory
```

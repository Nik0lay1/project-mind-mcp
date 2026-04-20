# ProjectMind MCP — Agent Instructions

Instructions for AI coding assistants (Zencoder, Claude, Cursor, etc.) that have
ProjectMind MCP configured.

The MCP server is registered as `ProjectMind` (or whatever alias user chose in
their `mcp.json`). All tool names follow the pattern `{server_alias}__{tool}`.
The examples below use the canonical `ProjectMind__` prefix.

---

## Session Start (REQUIRED)

At the beginning of **every** session, run **one** bootstrap call:

```
ProjectMind__session_init(project_path="<absolute path to target project>")
```

This single call atomically:

1. Sets the project root (so indices and memory land in the correct folder)
2. Reads `.ai/memory.md`
3. Reports index stats (chunk count)
4. Runs incremental re-index for files changed since last session
5. Returns a consolidated brief

If `project_path` is omitted, the server uses auto-detection (CWD-based). This
is only safe when the MCP was launched **from** the target project directory.

### Fallback (manual bootstrap)

If `session_init` is unavailable, run these in order:

1. `ProjectMind__set_project_root(path="<absolute path>")` — mandatory if the
   MCP server lives outside the target project
2. `ProjectMind__health()` — verify server, memory, and index status
3. `ProjectMind__read_memory()` — load saved project context
4. `ProjectMind__get_index_stats()` — verify semantic index
5. If chunks == 0 → `ProjectMind__index_codebase()`

---

## During Work

### Searching the codebase
- `ProjectMind__search_for_feature(feature_name)` — find where a feature lives
- `ProjectMind__search_codebase(query)` — general semantic search
- `ProjectMind__search_architecture(component)` — understand module structure
- `ProjectMind__get_file_relations(path)` — direct imports/importers
- `ProjectMind__get_dependencies_with_depth(path, depth=2)` — traverse graph

### After making code changes
```
ProjectMind__index_changed_files
```
Fast — re-indexes only modified files.

### Saving important decisions
```
ProjectMind__update_memory(content="...", section="Recent Decisions")
```
Duplicate content for the same section is automatically skipped.

---

## Rules

- **Always** call `session_init` or `set_project_root` first. Never assume the
  server points at the right project.
- Never commit the `.ai/` directory — it contains local embeddings and logs.
- After editing source files, run `index_changed_files`.
- Save architectural decisions with `update_memory`.
- Vector store is fully local — no API keys required.

---

## Key Files (the MCP codebase itself)

| File | Purpose |
|---|---|
| `mcp_server.py` | All MCP tool definitions |
| `vector_store_manager.py` | ChromaDB wrapper + query cache |
| `codebase_indexer.py` | File scanning + AST-aware chunking |
| `ast_splitter.py` | tree-sitter parser (Python, JS, TS, TSX, Java, Go, Rust, Ruby) |
| `bm25_index.py` | BM25 keyword index + Reciprocal Rank Fusion |
| `run_index.py` | Helper script for manual full re-indexing |
| `config.py` | All configuration (model, paths, extensions) |
| `context.py` | Dependency injection (AppContext singleton) |
| `memory_manager.py` | Read/write/version `.ai/memory.md` |
| `code_intelligence.py` | Import graph, complexity analysis |
| `incremental_indexing.py` | Track changed files via mtime |

## Key Configuration (`config.py`)

- **Embedding model**: `flax-sentence-embeddings/st-codesearch-distilroberta-base` (code-trained, ~130MB)
- **Chunk size**: 1500 chars / 150 overlap
- **Chunking strategy**: AST-aware (tree-sitter) → falls back to text splitter for unsupported types
- **Search strategy**: Hybrid — BM25 (keyword) + ChromaDB (semantic) merged via RRF
- **BM25 index**: persisted at `.ai/bm25_index.pkl`, rebuilt automatically
- **Vector store**: ChromaDB persistent at `.ai/vector_store/`
- **Memory file**: `.ai/memory.md`
- **Index ignore file**: `.indexignore` in project root (fallback: `.ai/.indexignore`)

## Commands

```bash
# Re-index from scratch (when model changes or full reset needed)
.venv/Scripts/python.exe run_index.py

# Run linter
.venv/Scripts/ruff check .

# Run tests
.venv/Scripts/pytest tests/
```

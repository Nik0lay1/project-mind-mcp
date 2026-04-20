# ProjectMind MCP — AI Instructions

> Canonical agent instructions live in [AGENTS.md](./AGENTS.md). This file is
> kept for Claude-specific clients and mirrors the same content.

## Session Start (REQUIRED)

At the beginning of **every** session, run **one** bootstrap call:

```
Memory__session_init(project_path="<absolute path to target project>")
```

If `project_path` is omitted, auto-detection is used — only safe when the MCP
was launched from the target project directory.

### Fallback (manual bootstrap)

If `session_init` is unavailable:

1. `Memory__set_project_root(path="<absolute path>")` — MANDATORY when the MCP
   server lives outside the target project.
2. `Memory__health` — sanity check of server, memory, index.
3. `Memory__read_memory` — load saved context.
4. `Memory__get_index_stats` — verify the semantic index.
5. If chunks == 0 → `Memory__index_codebase`.

> The server registers itself as `ProjectMind`, but the tool prefix matches the
> alias used in your `mcp.json`. Examples below use `Memory__` to match the
> historical Claude configuration.

## During Work

### Searching the codebase
- `Memory__search_for_feature` — find where a feature is implemented
- `Memory__search_codebase` — general semantic search
- `Memory__search_architecture` — understand module structure
- `Memory__get_file_relations` — see what imports what
- `Memory__get_dependencies_with_depth` — trace dependency chains

### After making code changes
```
Memory__index_changed_files
```

### Saving important decisions
```
Memory__update_memory  ←  section: "Recent Decisions", content: "..."
```
Duplicate entries under the same section are skipped automatically.

## Project Structure

| File | Purpose |
|---|---|
| `mcp_server.py` | All MCP tool definitions (~2500 lines) |
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
- **Chunk metadata**: `symbol_type`, `symbol_name`, `class_name`, `line_start`, `line_end`
- **Search strategy**: Hybrid — BM25 (keyword) + ChromaDB (semantic) merged via Reciprocal Rank Fusion
- **BM25 index**: persisted at `.ai/bm25_index.pkl`, rebuilt automatically after every indexing run
- **Vector store**: ChromaDB persistent at `.ai/vector_store/`
- **Memory file**: `.ai/memory.md`
- **Index ignore**: `.indexignore` in project root (fallback: `.ai/.indexignore`)

## Commands

```bash
# Re-index from scratch (when model changes or full reset needed)
.venv/Scripts/python.exe run_index.py

# Run linter
.venv/Scripts/ruff check .

# Run tests
.venv/Scripts/pytest tests/
```

## Rules

- **Always** call `session_init` or `set_project_root` first. Never assume the
  server points at the right project.
- Never commit `.ai/` (it's in `.gitignore`).
- Always run `Memory__index_changed_files` after editing source files.
- Save architectural decisions to memory using `Memory__update_memory`.
- The vector store uses a **local** model — no API keys needed.

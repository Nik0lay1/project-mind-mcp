# ProjectMind MCP — AI Instructions

## Session Start (REQUIRED)

At the beginning of **every** session, run these tools in order:

1. `Memory__read_memory` — load saved project context and decisions
2. `Memory__get_index_stats` — verify the semantic index exists and has chunks

If `get_index_stats` returns "not initialized" or 0 chunks:
- Run `Memory__index_codebase` (or via terminal: `.venv/Scripts/python.exe run_index.py`)

## During Work

### Searching the codebase
- `Memory__search_for_feature` — find where a feature is implemented
- `Memory__search_codebase` — general semantic search
- `Memory__search_architecture` — understand module structure
- `Memory__get_file_relations` — see what imports what
- `Memory__get_dependencies_with_depth` — trace dependency chains

### After making code changes
```
Memory__index_changed_files   ← re-index only changed files (fast)
```

### Saving important decisions
```
Memory__update_memory  ←  section: "Recent Decisions", content: "..."
```

## Project Structure

| File | Purpose |
|---|---|
| `mcp_server.py` | All MCP tool definitions (~2300 lines) |
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

## Key Configuration (config.py)

- **Embedding model**: `flax-sentence-embeddings/st-codesearch-distilroberta-base` (code-trained, ~130MB)
- **Chunk size**: 1500 chars / 150 overlap
- **Chunking strategy**: AST-aware (tree-sitter) → falls back to text splitter for unsupported types
- **Chunk metadata**: `symbol_type`, `symbol_name`, `class_name`, `line_start`, `line_end`
- **Search strategy**: Hybrid — BM25 (keyword) + ChromaDB (semantic) merged via Reciprocal Rank Fusion
- **BM25 index**: persisted at `.ai/bm25_index.pkl`, rebuilt automatically after every indexing run
- **Vector store**: ChromaDB persistent at `.ai/vector_store/`
- **Memory file**: `.ai/memory.md`

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

- Never commit `.ai/` directory (it's in `.gitignore`)
- Always run `Memory__index_changed_files` after editing source files
- Save architectural decisions to memory using `Memory__update_memory`
- The vector store uses a **local** model — no API keys needed

# ProjectMind MCP

> Give your AI coding assistant a brain. Persistent memory, semantic code search, a symbol-level call graph, and project intelligence — all running locally with no API keys required.

[![CI](https://github.com/Nik0lay1/project-mind-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Nik0lay1/project-mind-mcp/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-0.10.0-orange)
[![PyPI](https://img.shields.io/pypi/v/projectmind-mcp)](https://pypi.org/project/projectmind-mcp/)

**ProjectMind** is an open-source [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that supercharges AI assistants like Claude, Zencoder, and Cursor with long-term project memory and intelligent codebase search.

> 🤖 **This project was built with AI** — designed, coded, debugged, and documented using AI-assisted development from day one.

---

## Why ProjectMind?

Every time you start a new AI session, your assistant forgets everything about your project. ProjectMind solves this:

- **No more re-explaining** your architecture every session — memory persists in `.ai/memory.md`
- **Semantic code search** that understands *what* code does, not just *what* it's named
- **Symbol graph** — ask "who calls this function?" and get exact file:line answers
- **Dependency graph analysis** to understand how modules connect
- **Works 100% locally** — your code never leaves your machine

**New in v0.10.0**: 🧭 one shared file-selection path — `.indexignore` now governs the symbol graph and the manifest too, so generated output (`.next`, `dist`) stops crowding out your source; partial graphs report *why* instead of answering "no symbols found".

📝 See [CHANGELOG.md](CHANGELOG.md) for the full release history.

---

## Features

### 🧠 Persistent Project Memory
Save architectural decisions, tech stack notes, and context that survives across sessions. The AI reads this at the start of every conversation.

Hierarchical memory access avoids dumping everything at once:
- `read_memory_index()` — section headings only (cheap)
- `read_memory_section(name)` — expand only the section you need
- `search_memory(query)` — **relevance-ranked** retrieval of the memory blocks most relevant to a task (keyword-scored), not just the head of the file

All memory I/O is UTF-8 — notes in any language survive round-trips on any OS.

### 🪜 Tiered Hierarchical Search
Queries escalate through tiers only when the previous one is insufficient — large repositories never trigger a cold load just to answer a path lookup.

| Tier | Engine | When used | Typical latency |
|---|---|---|---|
| **L0 Manifest** | `.ai/manifest.json` (paths + whole-file symbols) | always | < 50 ms |
| **L0 Annotations** | `.ai/annotations.json` (AI-written summaries) | always | < 5 ms |
| **L1 Symbol** | `.ai/symbol_graph.json` (AST symbol names) | when a prebuilt graph exists | < 10 ms |
| **L1 BM25** | `rank-bm25` lexical index (chunks + annotations) | when L0 is weak | ~ 100 ms |
| **L2 Vector** | ChromaDB + sentence-transformers (**optional** `[vector]` extra) | only on `intent='semantic'/'deep'` | first call ~ 30 s, then cached |

Use the unified `query(text, intent, n_results)` tool with one of:
- `overview` — L0 only (paths + top-level symbols)
- `lookup` — L0 + symbols + BM25 (keyword/lexical)
- `semantic` — escalates to embeddings if the lexical signal is weak
- `deep` — all tiers with relaxed thresholds

Hits from every tier are fused with **scale-invariant Reciprocal Rank Fusion** (normalized to 0..1), so no single tier can dominate the ranking by raw score magnitude — results corroborated across tiers float to the top.

### ✍️ AI-Authored Annotations — semantic search without embeddings
The killer pattern: your AI assistant *already understands your code* — so let it write the search index. As the assistant works on files, it saves 1-2 sentence summaries + keywords via `save_annotation()`. Annotations are indexed into BM25 and a dedicated query tier, so natural-language queries land on the right files with plain keyword search:

```
save_annotation("src/auth/session.py",
    "Session lifecycle: creates, refreshes and revokes OAuth sessions",
    keywords="login, oauth, token refresh")

query("where are sessions revoked?")  →  src/auth/session.py  (L0_annot tier)
```

- `list_unannotated_files()` — coverage report: files missing or with stale annotations (file changed since)
- `get_annotations(path)` — review what the index "knows" about a file
- Stored in human-readable `.ai/annotations.json`, UTF-8, atomic writes
- This is why the vector stack is now optional: a smart model + cheap precise tools beats a small embedding model guessing

### 🧬 Symbol Graph (AST-level call/inherit/implement graph)
tree-sitter parses every source file into a graph of *symbols*, not files. Symbols are keyed by qualified id (`path::Class.method`), so same-named symbols across files stay distinct.

- `find_symbol("UserService")` — where is it defined? (no embedding model needed, answers in milliseconds)
- `get_symbol_relations("save", relation="callers")` — who calls it, with file:line
- Relations: `callers`, `callees`, `implementors`, `subclasses`, `bases`, `usages`, `info`
- Inheritance extraction works across **Python, JS, TS, Java, Ruby**; builtin-call noise (`print`, `len`, `push`…) is filtered out
- The graph is rebuilt automatically by the background indexer and persisted to `.ai/symbol_graph.json`
- File selection is shared with the indexer, so `.indexignore` keeps generated output (`.next`, `dist`) out of the graph — see [Ignore patterns](#ignore-patterns)
- A build that runs out of files or time is reported as **partial** by `find_symbol` and `health`, never as "no symbols found"

### 🔍 Semantic Code Search
Search your codebase by *meaning*, not just text. Powered by a local `sentence-transformers` model — no OpenAI key needed.

```
"find authentication middleware"  →  finds auth code even if it's named differently
```

### 🌳 AST-Aware Chunking
Unlike naive text splitters that cut code in the middle of a function, ProjectMind uses [tree-sitter](https://tree-sitter.github.io) to parse source files into **exact syntax units**:

- Functions and methods are indexed as individual, self-contained chunks
- Class methods get a `# Class: ClassName` context prefix for better search relevance
- Rich metadata per chunk: `symbol_type`, `symbol_name`, `class_name`, `line_start`, `line_end`
- Supports: **Python, JavaScript, TypeScript, TSX, Java, Go, Rust, Ruby**
- Graceful fallback to text splitting for unsupported file types

### 🕸 Dependency Graph Intelligence
- Traverse import relationships up to 5 levels deep
- Find related files via shared dependency clustering
- Discover the shortest path between any two modules
- Identify entry points and orphaned modules
- **Monorepo-aware JS/TS resolution** — follows `tsconfig`/`jsconfig` path aliases (`@/...`) and workspace/package imports; multi-line, dynamic `import()` and side-effect imports are all detected
- **Python `src/`-layout & relative-import resolution** — absolute imports resolve through `src/`/`lib/` roots, dotted (`utils.helpers`) and relative (`.`/`..`) imports resolve to the right files
- **Cached import graph** (120 s TTL) with a precomputed reverse graph — impact/cluster analysis is O(E), not O(N²·E)

### ⚡ Instant Project Exploration (no indexing needed)
- `get_project_overview()` — manifest-first; tech stack, git info, file stats in < 1 second
- `explore_directory(path)` — browse project tree level by level
- `get_file_summary(path)` — imports, classes, functions, git history

### ⚡ Hybrid Search (BM25 + Vector)
Two search engines combined via **Reciprocal Rank Fusion (RRF)**:
- **BM25** catches exact keyword matches — finds `getUserById` when you type exactly that
- **Vector search** catches semantic matches — finds auth code even if named differently
- RRF merges both ranked lists for best-of-both-worlds results
- Automatic fallback to pure vector search when the BM25 index is not ready
- The BM25 index is persisted as **JSON** (`.ai/bm25_index.json`) — never pickle, so indexing a third-party repo can't execute untrusted input

### 🔄 True Incremental Indexing
`index_changed_files` re-indexes only what changed — and cleans up after itself:
- A changed file's **old chunks are deleted** before the new ones land (renamed/removed symbols don't haunt search results)
- Chunks of **deleted files** are removed from ChromaDB, BM25 and the metadata
- BM25 is patched **in memory** per file and rebuilt once — no full-database fetch per small change
- Failed writes are never silent: metadata isn't saved, so affected files retry on the next run

### 🏃 Background Indexing
`index_codebase()` returns instantly and indexes in a daemon thread; `session_init` auto-starts it when the index is missing. Poll `get_index_progress()` for a live progress bar with ETA. Switching projects mid-run cancels the old job safely — no cross-project contamination.

### 🩺 Self-Healing Maintenance Daemon
A background thread keeps the index lean without user intervention. State persists in `.ai/maintenance_state.json`.

| Task | Trigger | Action |
|---|---|---|
| `manifest_refresh` | every 5 min | rebuild L0 manifest if files changed |
| `stale_gc` | hourly | delete embeddings for files removed from disk |
| `db_compaction` | daily | `VACUUM` ChromaDB SQLite when > 200 MB |
| `log_truncate` | every 6 h | truncate `projectmind.log` when > 8 MB |
| `model_unload` | every 5 min | release `sentence-transformers` after 1 h idle (env-tunable) |
| `cache_pressure` | every minute | drop file/query caches when RSS > 500 MB |

Inspect with `maintenance_status()`; force a sync run with `maintenance_run()`; aggressively clean the index with `prune_index(force=True)`.

### 📊 Code Quality Metrics
Cyclomatic complexity, pylint scores, test coverage tracking — all queryable via MCP tools.
Both `analyze_code_complexity` and `analyze_code_quality` accept `mode='quick'` (default, fast) or `mode='deep'` (wider scan). pylint runs in a subprocess with a wall-clock budget, so these tools return partial results instead of timing out.

### ⚡ Lazy `session_init` (no 30 s timeouts)
`session_init` never loads the embedding model; it returns the project root + manifest + memory index in well under a second even on multi-GB repositories. The vector store is loaded only when an `intent='semantic'` or `'deep'` query actually needs it.

---

## Quick Start

### One-liner (recommended)

```bash
# Claude Code
claude mcp add --scope user Memory -- uvx projectmind-mcp
```

The default install is **lightweight** (a few MB — BM25 keyword search, AI annotations, symbol graph, memory). To add the optional embedding/vector tier (~500 MB, fully local):

```bash
claude mcp add --scope user Memory -- uvx --from "projectmind-mcp[vector]" projectmind-mcp
```

**Other MCP clients (Claude Desktop / Zencoder / Cursor)** — `mcp.json`:

```json
{
  "mcpServers": {
    "Memory": {
      "command": "uvx",
      "args": ["projectmind-mcp"]
    }
  }
}
```

### From source (development)

```bash
git clone https://github.com/Nik0lay1/project-mind-mcp.git
cd project-mind-mcp
python -m venv .venv

# Windows                          # macOS/Linux
.venv\Scripts\pip install -e ".[vector,dev]"   # .venv/bin/pip install -e ".[vector,dev]"
```

Then point your MCP client at `.venv/Scripts/python.exe mcp_server.py` (or use the `projectmind-mcp` console script from the venv).

### 3. Bootstrap a session

Ask the AI (once per session):
```
Memory__session_init(project_path="<absolute path to your project>")
```

If the index doesn't exist yet, background indexing starts automatically — check `Memory__get_index_progress()`. For manual full re-indexing of large projects:

```bash
# Windows
.venv\Scripts\python.exe run_index.py

# macOS/Linux
.venv/bin/python run_index.py
```

---

## Available Tools (45+)

| Category | Tools |
|---|---|
| **Session** | `session_init`, `health`, `set_project_root` |
| **Memory** | `read_memory`, `read_memory_index`, `read_memory_section`, `search_memory`, `update_memory`, `clear_memory`, `save_memory_version` |
| **Search** | `query` (tier-aware), `search_codebase`, `search_for_feature`, `search_architecture`, `search_for_errors`, `search_with_dependencies` |
| **Context brief** | `get_context_brief` — ranked, budget-packed brief for a coding task: hybrid retrieval + 1-hop import graph + file skeletons + git recency; zero LLM calls (the "scout pyramid" layer 0) |
| **Annotations** | `save_annotation`, `get_annotations`, `list_unannotated_files` |
| **Symbols** | `find_symbol`, `get_symbol_relations` (callers / callees / implementors / subclasses / bases / usages) |
| **Exploration** | `get_project_overview`, `explore_directory`, `get_file_summary` |
| **Dependencies** | `get_file_relations`, `get_dependencies_with_depth`, `get_module_cluster`, `find_dependency_path`, `analyze_change_impact` |
| **Indexing** | `index_codebase` (background by default), `index_changed_files`, `get_index_progress`, `get_index_stats`, `prune_index` |
| **Git** | `ingest_git_history`, `get_recent_changes_summary`, `auto_update_memory_from_commits` |
| **Quality** | `analyze_code_complexity`, `analyze_code_quality`, `get_test_coverage_info` |
| **Maintenance** | `maintenance_status`, `maintenance_run` |
| **Project** | `detect_project_conventions`, `generate_project_summary` |

Full reference: [docs/api/tools-reference.md](docs/api/tools-reference.md)

---

## How It Works

```
Your Project
     │
     ▼
ProjectMind MCP Server
     │
     ├── .ai/memory.md                ← persistent notes & decisions (UTF-8)
     ├── .ai/annotations.json         ← AI-written file summaries (search tier)
     ├── .ai/manifest.json            ← L0: paths, symbols, modules (≤200 KB)
     ├── .ai/symbol_graph.json        ← L1: AST call/inherit/implement graph
     ├── .ai/bm25_index.json          ← L1: lexical index (JSON, not pickle)
     ├── .ai/vector_store/            ← L2: ChromaDB embeddings (local)
     ├── .ai/index_metadata.json      ← tracks changed files (mtime)
     ├── .ai/index_progress.json      ← live background-indexing progress
     ├── .ai/maintenance_state.json   ← self-healing daemon schedule
     └── .ai/.indexignore             ← per-project ignore patterns
     │
     ▼
AI Assistant (Claude / Zencoder / Cursor)
```

**Embedding model**: `flax-sentence-embeddings/st-codesearch-distilroberta-base`
- Trained specifically on code (CodeSearchNet dataset)
- ~130 MB, runs fully locally on CPU
- No API keys, no data sent anywhere

**Search pipeline**: manifest + symbol graph + BM25 (keyword) + ChromaDB (semantic) → Reciprocal Rank Fusion → top-N results

---

## Requirements

- Python 3.10 – 3.12
- ~500 MB disk (model + dependencies)
- Works on Windows, macOS, Linux

---

## Configuration

All settings in `config.py`:

| Setting | Default | Description |
|---|---|---|
| `MODEL_NAME` | `flax-sentence-embeddings/st-codesearch-distilroberta-base` | Embedding model |
| `CHUNK_SIZE` | `1500` | Characters per chunk |
| `MAX_FILE_SIZE_MB` | `10` | Skip files larger than this |
| `MAX_MEMORY_MB` | `100` | Memory limit for indexing batch |
| `IMPORT_GRAPH_MAX_FILES` | `8000` | Max files scanned when building the import graph |
| `TOOL_SOFT_BUDGET_SECONDS` | `20` | Wall-clock budget for analysis tools — they return partial results instead of timing out |

Override via environment variables:
```bash
PROJECTMIND_MAX_FILE_SIZE_MB=5
PROJECTMIND_MAX_MEMORY_MB=200
PROJECTMIND_IMPORT_GRAPH_MAX_FILES=20000
PROJECTMIND_SYMBOL_GRAPH_MAX_FILES=8000
PROJECTMIND_SYMBOL_GRAPH_BUDGET_SECONDS=300
PROJECTMIND_TOOL_BUDGET_SECONDS=45
PROJECTMIND_MODEL_IDLE_UNLOAD_SECONDS=3600
```

### Ignore patterns

Create `.indexignore` in the project root (fallback: `.ai/.indexignore`), same
substring syntax as shown in the generated default, plus globs like `*.min.js`.
Patterns match the project-relative path, and a matching **directory is pruned**
— never descended into.

One file drives every consumer: the vector index, the BM25 corpus, the symbol
graph and the manifest all select files through `file_scanner.py`. Excluding
build output matters for more than disk space — on a Next.js repo, `app/.next`
held 6 409 of 7 354 parseable files and sorted *first*, so a symbol graph that
ignored it burned its whole budget on minified bundles:

```
node_modules
.next
dist
*.min.js
```

---

## Project Structure

```
mcp_server.py           ← all MCP tool definitions
config.py               ← configuration + path validation
manifest.py             ← L0 lightweight project manifest
symbol_graph.py         ← AST symbol graph (call/inherit/implement), format v3
query_router.py         ← tier-aware query() router (L0 → L1_symbol → L1 → L2)
maintenance.py          ← self-healing background daemon
background_indexer.py   ← non-blocking indexing with live progress
vector_store_manager.py ← ChromaDB wrapper + hybrid search (L2)
bm25_index.py           ← BM25 keyword index + RRF fusion (L1, JSON persistence)
file_scanner.py         ← single file-selection path (.indexignore + dir pruning) shared by all consumers
codebase_indexer.py     ← file scanning & AST-aware chunking
ast_splitter.py         ← tree-sitter parser (9 languages, thread-safe)
code_intelligence.py    ← import graph, complexity analysis, cached graphs
memory_manager.py       ← persistent memory read/write (UTF-8, atomic)
incremental_indexing.py ← change tracking (mtime) + atomic writes
context.py              ← dependency injection (thread-safe lazy init)
run_index.py            ← helper script for manual re-indexing
```

---

## Contributing

Issues and PRs are welcome. This is an open project — built in the open, improved in the open.

```bash
pip install -e ".[dev]"
pytest tests/
ruff check .
```

CI runs ruff, black, mypy and the unit suite on Python 3.10–3.12 (see [.github/workflows/ci.yml](.github/workflows/ci.yml)).

---

## License

MIT

---

*Built with AI assistance — used throughout development for coding, debugging, refactoring, and documentation.*

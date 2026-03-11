# ProjectMind MCP

> Give your AI coding assistant a brain. Persistent memory, semantic code search, and project intelligence — all running locally with no API keys required.

**ProjectMind** is an open-source [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that supercharges AI assistants like Claude, Zencoder, and Cursor with long-term project memory and intelligent codebase search.

> 🤖 **This project was built with AI** — designed, coded, debugged, and documented using AI-assisted development from day one.

---

## Why ProjectMind?

Every time you start a new AI session, your assistant forgets everything about your project. ProjectMind solves this:

- **No more re-explaining** your architecture every session
- **Semantic code search** that understands *what* code does, not just *what* it's named
- **Dependency graph analysis** to understand how modules connect
- **Works 100% locally** — your code never leaves your machine

---

## Features

### 🧠 Persistent Project Memory
Save architectural decisions, tech stack notes, and context that survives across sessions. The AI reads this at the start of every conversation.

### 🔍 Semantic Code Search
Search your codebase by *meaning*, not just text. Powered by a local `sentence-transformers` model — no OpenAI key needed.

```
"find authentication middleware"  →  finds auth code even if it's named differently
```

### 🕸 Dependency Graph Intelligence
- Traverse import relationships up to 5 levels deep
- Find related files via shared dependency clustering
- Discover the shortest path between any two modules
- Identify entry points and orphaned modules

### ⚡ Instant Project Exploration (no indexing needed)
- `get_project_overview()` — tech stack, git info, file stats in < 1 second
- `explore_directory(path)` — browse project tree level by level
- `get_file_summary(path)` — imports, classes, functions, git history

### 🔄 Incremental Indexing
Only re-indexes changed files — 10-100x faster than full re-indexing.

### 📊 Code Quality Metrics
Cyclomatic complexity, pylint scores, test coverage tracking — all queryable via MCP tools.

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/Nik0lay1/ProjectMindMCP.git
cd ProjectMindMCP
python -m venv .venv

# Windows
.venv\Scripts\pip install -e .

# macOS/Linux
.venv/bin/pip install -e .
```

### 2. Add to your MCP client

**Zencoder / Claude Desktop** — add to `mcp.json`:

```json
{
  "mcpServers": {
    "Memory": {
      "command": "/path/to/ProjectMindMCP/.venv/bin/python",
      "args": ["/path/to/ProjectMindMCP/mcp_server.py"]
    }
  }
}
```

**Windows example:**
```json
{
  "mcpServers": {
    "Memory": {
      "command": "F:\\Projects\\ProjectMindMCP\\.venv\\Scripts\\python.exe",
      "args": ["F:\\Projects\\ProjectMindMCP\\mcp_server.py"]
    }
  }
}
```

### 3. Index your project

In your target project, ask the AI:
```
Memory__index_codebase
```

Or run directly for large projects:
```bash
cd /path/to/ProjectMindMCP
.venv/Scripts/python.exe -c "
import config, sys
sys.argv = ['', '--project-root', '/path/to/your/project']
from context import get_context
from mcp_server import startup_check, load_index_ignore_patterns, get_ignored_dirs
startup_check()
ctx = get_context()
print(ctx.indexer.index_all(config.PROJECT_ROOT, get_ignored_dirs(), load_index_ignore_patterns(), force=True))
"
```

---

## Available Tools (40+)

| Category | Tools |
|---|---|
| **Memory** | `read_memory`, `update_memory`, `clear_memory`, `save_memory_version` |
| **Search** | `search_codebase`, `search_for_feature`, `search_architecture`, `search_for_errors` |
| **Exploration** | `get_project_overview`, `explore_directory`, `get_file_summary` |
| **Dependencies** | `get_file_relations`, `get_dependencies_with_depth`, `get_module_cluster`, `find_dependency_path` |
| **Indexing** | `index_codebase`, `index_changed_files`, `get_index_stats` |
| **Git** | `ingest_git_history`, `get_recent_changes_summary`, `auto_update_memory_from_commits` |
| **Quality** | `analyze_code_complexity`, `analyze_code_quality`, `get_test_coverage_info` |
| **Project** | `set_project_root`, `detect_project_conventions`, `generate_project_summary` |

Full reference: [docs/api/tools-reference.md](docs/api/tools-reference.md)

---

## How It Works

```
Your Project
     │
     ▼
ProjectMind MCP Server
     │
     ├── .ai/memory.md          ← persistent notes & decisions
     ├── .ai/vector_store/      ← ChromaDB embeddings (local)
     └── .ai/index_metadata.json ← tracks changed files
     │
     ▼
AI Assistant (Claude / Zencoder / Cursor)
```

**Embedding model**: `flax-sentence-embeddings/st-codesearch-distilroberta-base`
- Trained specifically on code (CodeSearchNet dataset)
- ~130MB, runs fully locally on CPU
- No API keys, no data sent anywhere

---

## Requirements

- Python 3.10 – 3.12
- ~500MB disk (model + dependencies)
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

Override via environment variables:
```bash
PROJECTMIND_MAX_FILE_SIZE_MB=5
PROJECTMIND_MAX_MEMORY_MB=200
```

Custom ignore patterns: create `.ai/.indexignore` (same syntax as `.gitignore`).

---

## Project Structure

```
mcp_server.py           ← all MCP tool definitions
config.py               ← configuration
vector_store_manager.py ← ChromaDB wrapper
codebase_indexer.py     ← file scanning & chunking
code_intelligence.py    ← import graph, complexity analysis
memory_manager.py       ← persistent memory read/write
incremental_indexing.py ← change tracking
context.py              ← dependency injection
```

---

## Contributing

Issues and PRs are welcome. This is an open project — built in the open, improved in the open.

```bash
pip install -e ".[dev]"
pytest tests/
ruff check .
```

---

## License

MIT

---

*Built with AI assistance — [Zencoder](https://zencoder.ai) was used throughout development for coding, debugging, refactoring, and documentation.*

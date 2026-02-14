# ProjectMind: Local Context & Memory MCP Server

ProjectMind is a standalone MCP server that gives AI coding assistants **persistent memory**, **progressive project exploration**, and **local vector search** capabilities. It works instantly on any project — no indexing required to start.

## Features

### Core Features
- **Self-Initialization**: Automatically sets up `.ai/` directory and `.gitignore`
- **Project Memory**: Persistent memory file (`.ai/memory.md`) readable via `project://memory`
- **Context Management**: Tools to update, clear, and delete memory sections
- **Git Integration**: Ingest git commit history into project memory to track development flow
- **Local RAG**: Index your codebase and search it using local embeddings

### Progressive Exploration (NEW in v0.6.0)
- **🔥 Instant Project Overview**: `get_project_overview()` — tech stack, git info, memory context, file stats in < 1 second
- **🔥 Drill-Down Navigation**: `explore_directory(path)` — browse project tree level by level with git change markers
- **🔥 Smart File Summaries**: `get_file_summary(path)` — imports, classes, functions, git history, memory mentions
- **No indexing required** — all three tools work instantly without ChromaDB or embeddings
- **Context-aware** — every response enriched with data from memory and git history

### Advanced Features
- **Smart Indexing**: 
  - **Incremental indexing** - Only re-index changed files (10-100x faster)
  - Filters binary and non-text files automatically
  - Configurable file size limits (default 10MB)
  - Custom ignore patterns via `.ai/.indexignore`
  - Supports 50+ programming languages and text formats
- **Advanced Search Filters**:
  - Filter by file types
  - Exclude directories
  - Minimum relevance threshold
  - Relevance scores in results
- **Automatic Memory Updates**:
  - Auto-summarize recent commits
  - Scheduled memory updates
  - Smart grouping by contributors
- **Code Quality & Metrics**:
  - Cyclomatic complexity analysis
  - Pylint quality checks
  - Test coverage tracking
  - Technical debt detection
- **Memory Versioning**:
  - Git-like versioning for memory
  - Timestamped snapshots
  - Easy rollback and restore
  - Version history tracking
- **Performance Caching & Optimization** (v0.4.0+):
  - Multi-layer caching system (LRU, TTL, File caches)
  - Intelligent project auto-detection (no manual config!)
  - Memory pagination to prevent context window exhaustion
  - Lazy vector store initialization (30-60s faster startup)
  - Structure analysis caching (5-minute TTL)
  - Optimized file system traversal (3x faster on large projects)
  - Reduced disk I/O for file operations
  - 5-minute query result caching
  - Performance monitoring via `get_cache_stats()`
  - Thread-safe implementations
- **Production-Ready Architecture** (v0.5.0):
  - **Dependency Injection** - `AppContext` replaces global singletons for better testability
  - **Custom Exception Hierarchy** - Domain-specific exceptions for better error handling
  - **Git Utilities Module** - Reusable git operations eliminating code duplication
  - **Structured Logging** - Enhanced logging with JSON context fields
  - Zero global state - full class-based design
  - Path validation security (directory traversal prevention)
  - Enhanced Unicode handling (multi-encoding support)
  - Atomic file operations (crash-safe)
  - Memory-limited indexing (OOM prevention)
  - Comprehensive test coverage (131 unit tests, 100% pass rate)
- **Input Validation**: All tools have parameter validation and error handling
- **Memory Management**: Clear memory, delete specific sections, maintain templates
- **Index Statistics**: Track the number of indexed chunks
- **Type Safety**: Full type hints throughout the codebase
- **Environment Configuration**: Customize via environment variables

## 📚 Documentation

**Quick Links:**
- 🚀 **[Getting Started Guide](docs/guides/getting-started.md)** - Installation, setup, first steps
- 📖 **[Complete API Reference](docs/api/tools-reference.md)** - All 25 tools with examples
- 💡 **[Advanced Usage Guide](docs/guides/advanced-usage.md)** - Power features and workflows
- 📁 **[Full Documentation](docs/)** - Complete documentation index

## Installation

### Prerequisites
- Python 3.10+
- `uv` (recommended) or `pip`

### Using `uv` (Recommended)

1. Clone or copy the server files to your project or a central location
2. Run the server directly:

```bash
uv run mcp_server.py
```

### Using `pip`

1. Install dependencies:

```bash
pip install -e .
# or for development
pip install -e ".[dev]"
```

2. Run the server:

```bash
python mcp_server.py
```

## IDE Configuration

### Zencoder.ai

Add ProjectMind as a custom MCP server in Zencoder's Tools settings.

**✨ NEW (v0.5.4)**: The server now **automatically detects** your project! No need to set `cwd` manually.

#### Global Installation (Recommended)

Install ProjectMind once and use it with **any project**:

```json
{
  "type": "stdio",
  "command": "f:/path/to/projectmind/.venv/Scripts/python.exe",
  "args": ["f:/path/to/projectmind/mcp_server.py"]
}
```

The server will automatically find your project by searching for markers like `.git`, `package.json`, `pyproject.toml`, etc.

#### Configuration Examples

**Windows Example:**
```json
{
  "type": "stdio",
  "command": "f:/Tools/ProjectMind/.venv/Scripts/python.exe",
  "args": ["f:/Tools/ProjectMind/mcp_server.py"]
}
```

**macOS/Linux Example:**
```json
{
  "type": "stdio",
  "command": "/opt/projectmind/.venv/bin/python",
  "args": ["/opt/projectmind/mcp_server.py"]
}
```

#### Manual Project Path (Optional)

If auto-detection doesn't work, you can specify the project path:

```json
{
  "type": "stdio",
  "command": "f:/path/to/projectmind/.venv/Scripts/python.exe",
  "args": [
    "f:/path/to/projectmind/mcp_server.py",
    "--project-root",
    "${workspaceFolder}"
  ]
}
```

Or set environment variable:
```json
{
  "type": "stdio",
  "command": "f:/path/to/projectmind/.venv/Scripts/python.exe",
  "args": ["f:/path/to/projectmind/mcp_server.py"],
  "env": {
    "PROJECT_ROOT": "${workspaceFolder}"
  }
}
```

### Claude Desktop / Cursor / VS Code

To use ProjectMind with other IDEs, add it to your MCP config.

**✨ NEW (v0.5.4)**: Project detection is now automatic! Just point to the server.

**Example config.json:**
```json
{
  "mcpServers": {
    "projectmind": {
      "command": "/opt/projectmind/.venv/bin/python",
      "args": ["/opt/projectmind/mcp_server.py"]
    }
  }
}
```

The server will automatically detect your project. If needed, you can manually specify:
```json
{
  "mcpServers": {
    "projectmind": {
      "command": "/opt/projectmind/.venv/bin/python",
      "args": ["/opt/projectmind/mcp_server.py", "--project-root", "/path/to/project"]
    }
  }
}
```

## Available Tools

### Progressive Exploration (Start Here!)

These tools work **instantly** without indexing or vector store initialization. Use them to understand any project before diving into code.

#### `get_project_overview()`
Returns a comprehensive project overview combining file system, memory, and git data:
- Project name, root path, active git branch, total commits
- Tech stack (detected from config files + memory)
- Project status and recent decisions (from memory)
- Root directories and file type statistics
- Recent git activity (last 7 days)

**Use case:** First thing to call when opening a new or unfamiliar project.

```
# PROJECT OVERVIEW: my-app

**Root**: `/home/user/my-app`
**Git**: branch `main`, 142+ commits
**Tech**: Node.js, Docker

## Tech Stack (from memory)
- Language: TypeScript
- Framework: Next.js 14

## Recent Activity (last 7 days)
- 2026-02-14 [a1b2c3d]: fix: resolve auth race condition
- 2026-02-13 [e4f5g6h]: feat: add payment webhook

## Recent Decisions (from memory)
- Switched from JWT to session-based auth
```

#### `explore_directory(path=".", depth=1, max_items=100)`
Browse the project tree level by level with git context:
- `path`: Directory path relative to project root (use `"."` for root)
- `depth`: How many levels deep (1-3, default 1)
- `max_items`: Maximum items to show (1-500, default 100)
- Files changed in the last 14 days are annotated with the commit message
- Memory mentions for the directory are shown at the bottom

**Use case:** Navigate project structure and spot recently changed areas.

```
# src/

controllers/
  authController.ts  (4.2KB)  [changed 2026-02-14: fix auth race condition]
  userController.ts  (3.1KB)
models/
utils/
  logger.ts  (1.2KB)  [changed 2026-02-13: add structured logging]

## Notes from memory
- Refactored src to use hexagonal architecture
```

#### `get_file_summary(path, max_lines=50)`
Get a detailed file summary with code structure, git history, and memory context:
- `path`: File path relative to project root
- `max_lines`: Lines of code preview to include (0-500, default 50)
- Shows imports, classes, functions (Python, JS/TS)
- Git history: last 5 commits that touched this file
- Memory mentions: any notes about this file from project memory

**Use case:** Understand a file before editing it.

```
# authController.ts

**Path**: `src/controllers/authController.ts`
**Size**: 4.2 KB
**Last changed**: 2026-02-14 15:30 by John
**Total changes**: 12+ commits

**Imports** (5):
  - `import { Request, Response } from 'express'`
  - `import { AuthService } from '../services/auth'`

**Exports** (3):
  - `export async function login(req: Request, res: Response)`

## Git History
- 2026-02-14 15:30 [a1b2c3d] fix: resolve auth race condition (John)
- 2026-02-10 09:15 [x7y8z9a] refactor: extract auth middleware (John)

## Notes from memory
- Switched from JWT to session-based auth in authController
```

### Memory Management

#### `read_memory(max_lines: int | None = 100)`
Returns the current state of the project memory.

**Parameters:**
- `max_lines`: Maximum number of lines to return (default: 100)
  - Use lower values (50-200) for quick summaries
  - Use `None` for full content
  - Prevents overwhelming context windows with large memory files

**Example:**
```python
# Quick summary (first 100 lines)
read_memory()

# First 50 lines
read_memory(max_lines=50)

# Full content (may be large!)
read_memory(max_lines=None)
```

#### `update_memory(content: str, section: str = "Recent Decisions")`
Appends information to the project memory file under specified section.

#### `clear_memory(keep_template: bool = True)`
Clears the memory file. If `keep_template=True`, preserves the basic structure.

#### `delete_memory_section(section_name: str)`
Deletes a specific section from the memory file.

### Codebase Indexing & Search

#### `index_codebase(force: bool = False)`
Indexes the codebase for vector search.
- `force`: If true, clears existing index and rebuilds it
- Respects `.ai/.indexignore` patterns
- Automatically filters binary files and large files

#### `search_codebase(query: str, n_results: int = 5)`
Searches the codebase using vector similarity.
- `query`: Search query (required, non-empty)
- `n_results`: Number of results (1-50, default 5)

#### `get_index_stats()`
Returns statistics about the current vector store (number of chunks).

**Performance Note:** This operation is very fast and doesn't trigger vector store initialization.

#### `index_changed_files()` 🆕
**Incremental indexing** - only indexes files that changed since last index.
- 10-100x faster for large codebases
- Tracks file modification times
- Automatically removes deleted files from index

**Use case:** Daily re-indexing, continuous development workflow.

#### `search_codebase_advanced(query, n_results=5, file_types=None, exclude_dirs=None, min_relevance=0.0)` 🆕
Advanced search with filters:
- `file_types`: List of extensions (e.g., `[".py", ".js"]`)
- `exclude_dirs`: Directories to skip (e.g., `["tests/", "docs/"]`)
- `min_relevance`: Minimum relevance score (0-1)

**Use case:** Precise search in specific parts of codebase.

```python
# Search only in Python files, exclude tests
search_codebase_advanced(
    "authentication",
    file_types=[".py"],
    exclude_dirs=["tests/"],
    min_relevance=0.5
)
```

### Project Analysis & Intelligence

#### `generate_project_summary()`
Generates comprehensive project summary including:
- Current memory state
- Recent git activity (last 5 commits)
- Codebase statistics
- Index stats

**Use case:** Quick project overview for onboarding or context refresh.

#### `extract_tech_stack()`
Automatically extracts technology stack from:
- `pyproject.toml` / `requirements.txt` (Python)
- `package.json` (JavaScript/Node.js)
- `Cargo.toml` (Rust)
- `go.mod` (Go)

**Use case:** Understand project dependencies without reading config files.

#### `analyze_project_structure()`
Analyzes and reports:
- Main directories by size
- File type distribution
- Configuration files present

**Use case:** Understand project organization and architecture.

#### `get_recent_changes_summary(days: int = 7)`
Provides summary of recent development activity:
- Total commits in period
- Contributors and their activity
- Recent commit messages
- `days`: Period to analyze (1-365, default 7)

**Use case:** Catch up after being away from project.

### Git Integration

#### `ingest_git_history(limit: int = 30)`
Reads git commit history and appends new commits to memory.
- `limit`: Number of recent commits to check (1-1000, default 30)

#### `auto_update_memory_from_commits(days=7, auto_summarize=True)` 🆕
Automatically updates memory with recent git activity:
- Analyzes commits from last N days
- Auto-summarizes if > 5 commits
- Groups by contributors
- Highlights key changes

**Use case:** Automated weekly memory updates.

### Code Quality & Metrics 🆕

#### `analyze_code_complexity(target_path=".")`
Analyzes code complexity using cyclomatic complexity:
- Identifies high-complexity functions (>10)
- Calculates average complexity
- Supports Python files

**Use case:** Find code that needs refactoring.

#### `analyze_code_quality(target_path=".", max_files=10)`
Runs pylint analysis on codebase:
- Counts errors, warnings, refactoring suggestions
- Convention issues
- Quality score

**Use case:** Code review, technical debt tracking.

#### `get_test_coverage_info()`
Reads test coverage reports:
- Parses `.coverage` and `htmlcov/` data
- Shows overall coverage percentage
- Provides links to detailed reports

**Use case:** Monitor test coverage trends.

### Memory Versioning 🆕

#### `save_memory_version(description="")`
Creates a versioned backup of memory:
- Timestamped snapshots
- Optional description
- Stored in `.ai/memory_history/`

**Use case:** Before major changes, milestone backups.

#### `list_memory_versions()`
Lists all saved memory versions with timestamps and descriptions.

#### `restore_memory_version(timestamp)`
Restores memory from a specific version:
- Auto-backs up current memory before restore
- Specify timestamp from list

**Use case:** Rollback after mistakes, recover old context.

```python
# Save before major refactoring
save_memory_version("Before microservices migration")

# List versions
list_memory_versions()

# Restore if needed
restore_memory_version("20241216_143022")
```

### Performance Monitoring 🆕 (v0.4.0)

#### `get_cache_stats()`
Returns performance statistics for all caches:
- **File Cache** - Hit rate for file read operations
- **Query Cache** - Hit rate for vector search results
- Detailed metrics: hits, misses, size, capacity
- TTL and expiration tracking for query cache

**Use case:** Monitor caching effectiveness, optimize performance.

```python
# Check cache performance
get_cache_stats()

# Output:
# File Cache (safe_read_text)
# - Hits: 150
# - Misses: 50
# - Hit Rate: 75.00%
# - Size: 42/50
#
# Query Cache (vector search)
# - Hits: 80
# - Misses: 20
# - Hit Rate: 80.00%
# - Size: 15/100
# - Expirations: 5
# - TTL: 300s
```

## Quick Start Examples

### New to the project? (Recommended flow)
```python
# 1. Get instant overview (< 1 sec, no indexing needed)
get_project_overview()

# 2. Explore the main directories
explore_directory("src", depth=2)

# 3. Understand a specific file
get_file_summary("src/controllers/authController.ts")

# 4. (Optional) Index for semantic search
index_codebase()
search_codebase("authentication login", n_results=10)
```

### Coming back after a break?
```python
# Quick overview shows recent activity + decisions from memory
get_project_overview()

# What happened in last 2 weeks?
get_recent_changes_summary(days=14)

# See what changed in specific directory
explore_directory("src/controllers")
```

### Daily workflow
```python
# Morning: quick overview (memory + git)
get_project_overview()

# Browse recently changed files
explore_directory("src", depth=2)

# Understand a file before editing
get_file_summary("src/services/payment.ts")

# Document decision
update_memory("Switched to JWT tokens for better scalability", 
              section="Architecture Decisions")
```

## Configuration

### Environment Variables

- `PROJECTMIND_MAX_FILE_SIZE_MB`: Maximum file size to index in MB (default: 10)

### Custom Ignore Patterns

Create `.ai/.indexignore` to exclude specific files from indexing:

```
# Example .indexignore
test_data/
*.log
legacy/
```

### Code Configuration

Edit `config.py` to customize:
- Chunk size and overlap for text splitting
- Ignored directories
- File type extensions
- Batch size for indexing

## Development

### Running Tests

```bash
# Run all tests
python test_mcp_tools.py

# Run search test
python test_search.py
```

### Pre-commit Hooks

Install pre-commit hooks for code quality:

```bash
pip install pre-commit
pre-commit install
```

This will run:
- Black (code formatting)
- Ruff (linting)
- MyPy (type checking)
- YAML/JSON/TOML validators

### Code Quality Tools

```bash
# Format code
black .

# Lint code
ruff check . --fix

# Type check
mypy mcp_server.py config.py --ignore-missing-imports
```

## Resources

### `project://memory`
Reads the project memory file directly as a resource.

## Architecture

### Components

**Core Server:**
- **mcp_server.py**: Main server with all MCP tools, including progressive exploration tools with memory/git context
- **config.py**: Centralized configuration management with security features
- **context.py**: Dependency injection via `AppContext`

**Class-Based Modules:**
- **vector_store_manager.py**: ChromaDB management with query caching
- **memory_manager.py**: Memory operations with versioning support
- **codebase_indexer.py**: File scanning, chunking, and indexing logic
- **git_utils.py**: Git operations (commits, file history, recently changed files, branch info)
- **cache_manager.py**: Multi-layer caching (LRU, TTL, File caches)
- **logger.py**: Centralized rotating file logging
- **incremental_indexing.py**: Atomic metadata operations

**Infrastructure:**
- **pyproject.toml**: Modern Python project configuration
- **.pre-commit-config.yaml**: Pre-commit hooks for code quality
- **.github/workflows/ci.yml**: CI/CD pipeline with 7 test suites
- **tests/**: Comprehensive unit test suite (62 tests)

### Technology Stack

- **MCP Server**: FastMCP
- **Vector Store**: ChromaDB (persistent)
- **Embeddings**: sentence-transformers (all-MiniLM-L6-v2)
- **Text Splitting**: LangChain RecursiveCharacterTextSplitter
- **Git Integration**: GitPython
- **Caching**: Custom LRU/TTL implementations
- **Testing**: unittest with mock isolation
- **Security**: Path validation, atomic file operations

## Troubleshooting

### Server hangs for 10-15 minutes on large projects
Use the progressive exploration tools (`get_project_overview`, `explore_directory`, `get_file_summary`) instead of `index_codebase`. These work instantly without loading ML models or indexing files. Only call `index_codebase` when you need semantic search.

### Vector store not initialized
The vector store is lazy-loaded on first use. Run `index_codebase()` first. The progressive exploration tools do **not** require the vector store.

### Large files being skipped
Files larger than 10MB are skipped by default. Set `PROJECTMIND_MAX_FILE_SIZE_MB` to increase the limit.

### Git history not showing
Ensure you're in a git repository. The tool searches parent directories automatically. If git is unavailable, the tools still work — they just skip git-related sections.

## Contributing

1. Fork the repository
2. Install development dependencies: `pip install -e ".[dev]"`
3. Install pre-commit hooks: `pre-commit install`
4. Make your changes
5. Run tests: `python test_mcp_tools.py`
6. Submit a pull request

## License

MIT License - feel free to use and modify as needed.

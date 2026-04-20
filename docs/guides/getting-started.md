# Getting Started with ProjectMind

Quick guide to start using ProjectMind MCP server in your projects.

## Installation

### Prerequisites
- Python 3.10+
- `uv` (recommended) or `pip`

### Install Dependencies

**Using uv (recommended):**
```bash
uv pip install -e .
```

**Using pip:**
```bash
pip install -e .
```

**Development dependencies:**
```bash
uv pip install -e ".[dev]"
```

---

## Setup

### 1. Add to Claude Desktop Config

Edit your Claude config file:
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%/Claude/claude_desktop_config.json`

Add ProjectMind server:
```json
{
  "mcpServers": {
    "projectmind": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/ProjectMind",
        "run",
        "mcp_server.py"
      ]
    }
  }
}
```

**Replace** `/absolute/path/to/ProjectMind` with your actual path.

### 2. Restart Claude Desktop

Close and reopen Claude Desktop to load the MCP server.

---

## First Steps

### Initialize Project Memory

ProjectMind auto-creates `.ai/` directory on first run:
```
.ai/
├── memory.md              # Project memory
├── vector_store/          # Embeddings database
├── index_metadata.json    # Incremental indexing
└── memory_history/        # Version backups
```

### Basic Workflow

**1. Index your codebase:**
```python
index_codebase()
```

**2. Search for code:**
```python
search_codebase("authentication logic")
```

**3. Update project memory:**
```python
update_memory("Using FastAPI for REST API", section="Tech Stack")
```

**4. Read memory:**
```python
read_memory()
```

---

## Common Use Cases

### Daily Development Workflow

**Morning sync:**
```python
# Get project overview
generate_project_summary()

# Check recent changes
get_recent_changes_summary(days=1)
```

**During development:**
```python
# Incremental indexing (fast!)
index_changed_files()

# Search specific code
search_codebase_advanced("error handling", file_types=[".py"])
```

**End of day:**
```python
# Auto-update memory with commits
auto_update_memory_from_commits(days=1)

# Save memory snapshot
save_memory_version("End of sprint 5")
```

### Code Review Session

```python
# Analyze complexity
analyze_code_complexity("src/")

# Check code quality
analyze_code_quality("src/", max_files=20)

# Review test coverage
get_test_coverage_info()
```

### Onboarding New Team Member

```python
# Project overview
generate_project_summary()

# Tech stack
extract_tech_stack()

# Project structure
analyze_project_structure()

# Recent activity
get_recent_changes_summary(days=30)
```

---

## Configuration

### Environment Variables

```bash
# Max file size for indexing (in MB)
export PROJECTMIND_MAX_FILE_SIZE_MB=20

# Max memory budget for a single indexing batch (in MB)
export PROJECTMIND_MAX_MEMORY_MB=200

# Embedding model used is configured in config.py:
# flax-sentence-embeddings/st-codesearch-distilroberta-base (~130MB, code-trained, local)
```

### .indexignore

Create `.indexignore` in your project root to exclude files from indexing
(fallback location: `.ai/.indexignore`):
```
# Ignore tests
tests/
test_*.py

# Ignore generated files
*.generated.py
migrations/

# Ignore documentation
docs/
*.md
```

Syntax: Same as `.gitignore`

---

## Tips & Best Practices

### Indexing

✅ **Do:**
- Use `index_changed_files()` for daily updates
- Use `index_codebase(force=True)` only when needed (major refactor)
- Keep `.indexignore` updated

❌ **Don't:**
- Index large binary files (auto-filtered)
- Index node_modules or venv (auto-filtered)
- Re-index entire codebase frequently

### Memory Management

✅ **Do:**
- Organize memory with clear sections
- Save versions before major changes
- Use `auto_update_memory_from_commits()` regularly

❌ **Don't:**
- Let memory grow too large (>10KB)
- Store code snippets (use indexing instead)
- Forget to version important states

### Search

✅ **Do:**
- Use descriptive queries
- Use `search_codebase_advanced()` for precision
- Set `min_relevance` to filter noise

❌ **Don't:**
- Use very generic queries ("function", "class")
- Set `n_results` too high (>20)
- Search before indexing

---

## Next Steps

- Read [API Reference](../api/tools-reference.md) for all tools
- Check [Advanced Usage](./advanced-usage.md) for power features
- Review [Feature Documentation](../features/) for deep dives

## Troubleshooting

### "Vector store not initialized"
Run `index_codebase()` first to create embeddings.

### "No coverage data found"
Run tests with coverage:
```bash
pytest --cov=. --cov-report=html
```

### Slow indexing
- Use `index_changed_files()` instead
- Add large files to `.indexignore`
- Check `PROJECTMIND_MAX_FILE_SIZE_MB`

---

**Ready to use ProjectMind!** 🚀

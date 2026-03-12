# ProjectMind API Reference

Complete reference for all MCP tools provided by ProjectMind.

## Memory Management

### `read_memory()`
Returns the current content of the project memory file.

**Returns:** String with memory content

**Example:**
```python
memory = read_memory()
```

---

### `update_memory(content: str, section: str = "General")`
Updates or appends content to a specific section in memory.

**Parameters:**
- `content` (str, required): Content to add (cannot be empty)
- `section` (str, optional): Section name (default: "General")

**Returns:** Success/error message

**Example:**
```python
update_memory("Using PostgreSQL for database", section="Tech Stack")
```

---

### `clear_memory()`
Clears all memory content and resets to template.

**Returns:** Confirmation message

**Example:**
```python
clear_memory()
```

---

### `delete_memory_section(section_name: str)`
Deletes a specific section from memory.

**Parameters:**
- `section_name` (str, required): Name of section to delete

**Returns:** Success/error message

**Example:**
```python
delete_memory_section("Old Notes")
```

---

## Codebase Indexing & Search

### `index_codebase(force: bool = False)`
Indexes the entire codebase for vector search.

**Parameters:**
- `force` (bool, optional): If True, clears and rebuilds index (default: False)

**Features:**
- Respects `.ai/.indexignore` patterns
- Filters binary files automatically
- File size limit: 10MB (configurable)
- Supports 50+ file types
- Maximum 5000 files per indexing operation

**Returns:** Index summary with file/chunk count

**Example:**
```python
index_codebase(force=True)   # Full reindex
```

---

### `index_changed_files()`
🆕 **Incremental indexing** - only indexes modified files.

**Features:**
- 10-100x faster than full indexing
- Tracks modification times
- Auto-removes deleted files
- Metadata stored in `.ai/index_metadata.json`

**Returns:** Summary of indexed changes

**Example:**
```python
index_changed_files()   # Daily workflow
```

---

### `search_codebase(query: str, n_results: int = 5)`
Searches indexed codebase using vector similarity.

**Parameters:**
- `query` (str, required): Search query (cannot be empty)
- `n_results` (int, optional): Number of results (1-50, default: 5)

**Returns:** Formatted search results with source files

**Example:**
```python
search_codebase("authentication logic", n_results=10)
```

---

### `search_codebase_advanced(query: str, n_results: int = 5, file_types: List[str] = None, exclude_dirs: List[str] = None, min_relevance: float = 0.0)`
🆕 Advanced search with filtering capabilities.

**Parameters:**
- `query` (str, required): Search query
- `n_results` (int, optional): Number of results (1-50, default: 5)
- `file_types` (list, optional): Filter by extensions (e.g., `[".py", ".js"]`)
- `exclude_dirs` (list, optional): Directories to skip (e.g., `["tests/", "docs/"]`)
- `min_relevance` (float, optional): Minimum relevance score 0-1 (default: 0.0)

**Returns:** Results with relevance scores

**Example:**
```python
search_codebase_advanced(
    "API endpoints",
    file_types=[".py"],
    exclude_dirs=["tests/"],
    min_relevance=0.5
)
```

---

### `get_index_stats()`
Returns statistics about the vector store.

**Returns:** Index stats (total chunks, files)

**Example:**
```python
stats = get_index_stats()
```

---

## Project Analysis

### `generate_project_summary()`
Generates comprehensive project overview.

**Includes:**
- Current memory state
- Recent git activity (last 5 commits)
- Codebase statistics
- Index stats

**Performance:** ⚡ Optimized in v0.5.1 - Uses single file system traversal (3x faster on large projects)

**Returns:** Formatted summary

**Example:**
```python
summary = generate_project_summary()
```

---

### `extract_tech_stack()`
Automatically extracts technology stack from config files.

**Supports:**
- Python: `pyproject.toml`, `requirements.txt`
- Node.js: `package.json`
- Rust: `Cargo.toml`
- Go: `go.mod`

**Returns:** List of dependencies and frameworks

**Example:**
```python
tech = extract_tech_stack()
```

---

### `analyze_project_structure()`
Analyzes codebase organization.

**Provides:**
- Directory sizes
- File type distribution
- Configuration files present

**Performance:** ⚡ Cached in v0.5.1 - Results cached for 5 minutes (configurable via `STRUCTURE_CACHE_TTL`)

**Returns:** Structure analysis

**Example:**
```python
structure = analyze_project_structure()
```

---

### `get_recent_changes_summary(days: int = 7)`
Summarizes recent development activity.

**Parameters:**
- `days` (int, optional): Period to analyze (1-365, default: 7)

**Returns:** Commit summary with contributors

**Example:**
```python
changes = get_recent_changes_summary(days=14)
```

---

## Git Integration

### `ingest_git_history(limit: int = 30)`
Reads git commits and appends to memory.

**Parameters:**
- `limit` (int, optional): Number of commits (1-1000, default: 30)

**Returns:** Ingestion summary

**Example:**
```python
ingest_git_history(limit=50)
```

---

### `auto_update_memory_from_commits(days: int = 7, auto_summarize: bool = True)`
🆕 Automatically updates memory with git activity.

**Parameters:**
- `days` (int, optional): Days to analyze (1-90, default: 7)
- `auto_summarize` (bool, optional): Smart summarization if >5 commits (default: True)

**Features:**
- Auto-groups by contributors
- Highlights key changes
- Prevents memory bloat

**Returns:** Update summary

**Example:**
```python
auto_update_memory_from_commits(days=14)
```

---

## Code Quality & Metrics

### `analyze_code_complexity(target_path: str = ".")`
🆕 Analyzes cyclomatic complexity.

**Parameters:**
- `target_path` (str, optional): Path to analyze (default: ".")

**Provides:**
- High-complexity functions (>10)
- Average complexity
- File-by-file breakdown

**Returns:** Complexity report

**Example:**
```python
complexity = analyze_code_complexity("src/")
```

---

### `analyze_code_quality(target_path: str = ".", max_files: int = 10)`
🆕 Runs pylint quality checks.

**Parameters:**
- `target_path` (str, optional): Path to analyze (default: ".")
- `max_files` (int, optional): Max files to check (default: 10)

**Reports:**
- Errors, warnings, refactoring suggestions
- Convention issues
- Quality score

**Returns:** Quality report

**Example:**
```python
quality = analyze_code_quality("src/", max_files=20)
```

---

### `get_test_coverage_info()`
🆕 Reads test coverage reports.

**Requires:** `.coverage` or `htmlcov/` directory

**Returns:** Coverage percentage and report links

**Example:**
```python
coverage = get_test_coverage_info()
```

---

## Memory Versioning

### `save_memory_version(description: str = "")`
🆕 Creates versioned memory snapshot.

**Parameters:**
- `description` (str, optional): Version description

**Storage:** `.ai/memory_history/`

**Returns:** Version filename

**Example:**
```python
save_memory_version("Before major refactor")
```

---

### `list_memory_versions()`
🆕 Lists all saved memory versions.

**Returns:** Formatted version history with timestamps

**Example:**
```python
versions = list_memory_versions()
```

---

### `restore_memory_version(timestamp: str)`
🆕 Restores memory from specific version.

**Parameters:**
- `timestamp` (str, required): Version timestamp (from `list_memory_versions()`)

**Safety:** Auto-backs up current memory before restore

**Returns:** Restoration confirmation

**Example:**
```python
restore_memory_version("20251216_093000")
```

---

## Tool Count: 22 Total

**Categories:**
- Memory Management: 4 tools
- Indexing & Search: 5 tools
- Project Analysis: 4 tools
- Git Integration: 2 tools
- Code Quality: 3 tools
- Memory Versioning: 3 tools

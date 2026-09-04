# ProjectMind MCP — Agent Instructions

ProjectMind MCP is configured as `Memory` — all tools use the prefix `Memory__`.

---

## Session Start (REQUIRED)

At the beginning of **every** session, call:

```
Memory__session_init(project_path="<absolute path to THIS project>")
```

This single call:
1. Points the MCP at this project (memory + index)
2. Loads `.ai/memory.md` with saved context
3. Reports index status
4. Runs incremental re-index for files changed since last session

> Always pass `project_path` explicitly — do NOT rely on auto-detection.

### Fallback (if session_init is unavailable)

1. `Memory__set_project_root(path="<absolute path>")` — MANDATORY first step
2. `Memory__health()` — verify server, memory, and index status
3. `Memory__read_memory()` — load saved project context
4. `Memory__get_index_stats()` — verify semantic index
5. If chunks == 0 → `Memory__index_codebase()`

---

## During Work

### Search
- `Memory__search_for_feature("feature name")` — find where a feature is implemented
- `Memory__search_codebase("query")` — semantic search by meaning
- `Memory__search_architecture("module")` — understand module structure
- `Memory__get_context_brief("task", budget_tokens=4000)` — deterministic ranked context brief for a coding task (hybrid search + import graph + skeletons), zero LLM calls
- `Memory__get_file_relations("path/to/file")` — what imports / is imported by a file
- `Memory__get_dependencies_with_depth("path/to/file", depth=2)` — dependency graph

### After making code changes
```
Memory__index_changed_files
```
Fast and non-blocking — runs incremental indexing in the background. Call `Memory__get_index_progress()` if you need to monitor status.

### Save important decisions
```
Memory__update_memory(content="...", section="Recent Decisions")
```
Duplicate entries are automatically skipped.

---

## Rules

- **Always** call `session_init` first — never assume the server points at the right project.
- Never commit `.ai/` — it contains local embeddings and memory (add to `.gitignore`).
- After editing source files, run `Memory__index_changed_files`.
- Save architectural decisions with `Memory__update_memory`.
- If index is empty (0 chunks) → run `Memory__index_codebase()`.
- Vector store is fully local — no API keys required.

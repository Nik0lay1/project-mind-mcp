# Changelog

## [0.9.0] - 2026-07-03 ✍️ AI-AUTHORED ANNOTATIONS + LIGHTWEIGHT CORE + PyPI

### Added — annotations (semantic search without embeddings)
- New `annotations.py` + `.ai/annotations.json`: the client LLM saves 1-2 sentence summaries + keywords per file (`save_annotation`), reviews them (`get_annotations`) and tracks coverage (`list_unannotated_files`, with staleness detection via file mtime).
- Annotations are indexed into the BM25 corpus as synthetic documents (merged on load and on every rebuild) and served by a new near-free `L0_annot` tier in `query()` — natural-language queries land on the right files via plain keyword search. The design follows the industry lesson that a smart model + precise cheap tools beats a small embedding model guessing.

### Changed — the vector stack is now optional
- `chromadb`, `sentence-transformers` and the numpy pin moved to the `[vector]` extra. The default install is a few MB: BM25 (chunks + annotations), symbol graph, import graph, memory.
- Full **BM25-only mode**: indexing (foreground + background + incremental) feeds the keyword corpus directly, `hybrid_query` serves BM25 results, `index_codebase(force=True)` clears the corpus, readiness checks and chunk counts read the BM25 index. Everything degrades gracefully; installing the extra upgrades in place.
- BM25 small-corpus fallback: when idf collapses to 0 (2-3 doc corpora), search falls back to token-overlap ranking instead of returning nothing.
- BM25 matrix now rebuilds lazily after corpus updates (one rebuild per burst instead of per file).

### Added — PyPI packaging
- Published as **`projectmind-mcp`**: `claude mcp add --scope user Memory -- uvx projectmind-mcp` (add `[vector]` for embeddings). Console scripts `projectmind-mcp` / `projectmind`; `main()` entry point.
- `.github/workflows/publish.yml`: build + twine check + publish on GitHub Release via PyPI Trusted Publishing (no token in repo).
- Proper metadata: license, classifiers, keywords, project URLs.

---

## [0.8.0] - 2026-07-02 🛠️ CORRECTNESS OVERHAUL + SYMBOL GRAPH V3

### Security
- **BM25 index is now JSON** (`.ai/bm25_index.json`), never pickle. The old `.pkl` lived inside the *target* project's `.ai/` dir, so unpickling it was an arbitrary-code-execution vector when indexing third-party repos. Legacy `.pkl` files are deleted on `clear()`.

### Fixed — index hygiene
- Re-indexing a changed file now **deletes its previous chunks** first (`delete_by_source`), and chunks of deleted files are removed from ChromaDB, BM25 and the metadata — renamed/removed symbols no longer haunt search results forever.
- **Failed upserts are no longer silent**: index metadata is not saved on failure, so affected files retry on the next run, and the tool output says so.
- `index_all` now records `IndexMetadata` (previously the next incremental run re-indexed everything); `prune_index(force=True)` resets it (previously `index_changed_files` reported "no changes" against an empty store).
- Query cache is invalidated on upsert / clear / BM25 rebuild (stale results were served for up to 5 minutes after re-indexing).
- Chunk IDs include `line_start`, so same-named symbols in one file no longer overwrite each other.
- mtime is captured *before* reading a file (TOCTOU) and compared with `!=` instead of `>`.

### Fixed — three dead tools
- `search_with_dependencies`, `search_for_errors`, `search_architecture` read a metadata key (`file_path`) that was never written (the indexer stores `source`) — all three silently returned empty sections. They now read the right key and normalize absolute Windows paths to the relative posix form the import graph uses.

### Fixed — graphs
- Python import extraction kept only the top-level package (`utils.helpers` → `utils`) and dropped relative imports entirely, making the v0.7.7 resolution machinery dead code. Full dotted/relative specifiers are now preserved.
- JS/TS import regex now matches multi-line (prettier-wrapped), dynamic `import()`, and side-effect imports.
- Symbol graph: operator-precedence bug made the call-noise filter accept everything (`print`, `len`, …) — replaced with a builtin blocklist; class nodes no longer double-attribute every call made inside their methods; inheritance extraction now works for JS/TS/Java/Ruby (was Python-only); call refs record the actual call-site line.

### Fixed — Windows & encoding
- `memory.md` is always read/written as UTF-8 (was locale-dependent: Cyrillic content could be corrupted and written back corrupted); `delete_section` matches headings exactly and writes atomically.
- `atomic_write` no longer unlinks the target before `os.replace` (which is already atomic on Windows) — no more crash window where the file doesn't exist.

### Fixed — concurrency
- `get_context()` and `AppContext.get_symbol_graph()` use double-checked locking (two concurrent tools could create two ChromaDB clients + load the model twice).
- tree-sitter parsing is serialized behind a shared lock (`Parser` objects are not thread-safe; concurrent parses could crash the interpreter).
- BM25 state swaps atomically under a lock (searches during rebuild saw mismatched id/score arrays).
- Background indexer initializes the vector store *before* a force-clear (the `session_init` auto-heal reliably died with "ChromaDB client not initialized"); progress dict is lock-protected.
- `set_project_root` / `session_init` now **cancel a running background index job** before switching roots (a job for project A could write A's chunks into B's store).

### Changed — Symbol Graph v3 (now actually wired in)
- Symbols are keyed by **qualified id** (`path::Class.name`) — same-named symbols across files no longer collapse into one node; `by_name` index resolves bare names to all definitions.
- New MCP tools: `find_symbol(name)` and `get_symbol_relations(symbol, relation)` (callers/callees/implementors/subclasses/bases/usages/info).
- New `L1_symbol` tier in `query()` — exact/substring symbol-name hits fuse with manifest/BM25/vector tiers via RRF (uses `peek_symbol_graph()`, never builds during a search).
- The background indexer rebuilds the symbol graph after each indexing run; staleness is checked against IndexMetadata mtimes instead of directory sampling.

### Changed — performance
- **Incremental BM25**: `index_changed_files` patches only the changed files' rows in the in-memory corpus and rebuilds the matrix once — no more full-ChromaDB fetch + full rewrite per small change.
- `get_module_cluster` / `analyze_change_impact` use a precomputed reverse import graph (were O(N²·E) full-graph rescans per node).
- `pylint` runs as a subprocess with JSON output (in-process runs swapped the global `sys.stdout`, risking MCP stdio protocol corruption).
- L2 query timeout actually times out (executor shutdown no longer blocks on the hung worker); memory-pressure relief uses current RSS, not lifetime peak (`ru_maxrss`), so it no longer fires forever after the model loads; manifest staleness respects the 20k-file scan cap (no more perpetual rebuild loop on huge repos).

### Meta
- `pyproject` `py-modules` now includes `symbol_graph`, `background_indexer`, `manifest`, `maintenance`, `query_router` (pip installs were broken without them); coverage no longer omits the biggest modules (the old 80% gate measured a fraction of the code — threshold reset to an honest 40%); version synced to 0.8.0 in README/pyproject.

---

## [0.7.8] - 2026-06-08 ⏱️ TOOL TIME BUDGETS (NO MORE TIMEOUTS)

### Fixed
- **`analyze_code_quality` no longer times out.** It ran `pylint` in-process, sequentially, on up to 10 files (quick) or 100 (deep). Benchmarked at ~52s for a single large file, this tool effectively *always* exceeded the MCP call timeout. It now enforces a soft wall-clock budget, stops after the budget is spent, and returns the partial summary with a note (always analyzes at least one file).
- **`build_import_graph` is now time-bounded.** The per-file resolution loop could run unbounded toward the 8000-file cap; it now stops at the budget, returns the partial graph, and logs a WARNING telling you edges are missing and how to raise the budget.
- **`analyze_code_complexity` is time-bounded too** (radon/AST is fast, but deep mode scans up to 1000 files) — same partial-result + note behavior.

### Added
- `config.TOOL_SOFT_BUDGET_SECONDS` (default `20`) + `get_tool_budget_seconds()`, overridable via `PROJECTMIND_TOOL_BUDGET_SECONDS` (accepts fractional seconds; non-positive/invalid values fall back to the default).
- Tests: `tests/test_tool_budget.py` covering the config getter (default, env override, fractional, invalid, non-positive, negative, return type) and the import-graph budget guard (stops early, emits warning, generous budget scans all).

### Why
- Tool timeouts were a frequent, user-reported problem. The audit pinpointed in-process pylint as the dominant offender and the import-graph loop as the scaling risk. A single, env-tunable time budget makes every heavy analysis tool return useful partial output within the MCP call window instead of failing.

---

## [0.7.7] - 2026-06-08 🐍 PYTHON SRC-LAYOUT & RELATIVE IMPORTS

### Fixed
- **Python relative imports** (`from .sibling import x`, `from ..pkg import y`, `from . import z`) were resolved by naively replacing dots with slashes, which on a relative specifier produced a drive-absolute path and silently dropped the edge. They now walk up the correct number of package levels from the importing file.
- **`src/` (and `lib/`) layout** — absolute imports like `import mypkg.foo` in a `src/`-layout project now resolve to `src/mypkg/foo.py`. Previously only the project root was probed, so the entire dependency graph of src-layout packages was invisible.

### Added
- `_python_search_roots` (cached) and `_python_import_candidates` helpers driving the Python branch of `_resolve_import_to_file`.
- Tests: `tests/test_python_resolution.py` covering src/lib roots, absolute + package-init resolution, single/double/bare-dot relative imports, root-escape rejection, and unresolvable imports.

### Why
- The import graph is the backbone of impact analysis, relations, clustering, and dependency-path search. Python is a first-class target, yet relative imports and the ubiquitous `src/` layout were blind spots — closing them materially improves context curation for Python repos.

---

## [0.7.6] - 2026-06-08 🕸️ CONFIGURABLE IMPORT-GRAPH CAP

### Changed
- **Import-graph file limit is now configurable and higher by default** — the dependency graph was hard-capped at 3000 files, so larger monorepos silently lost edges past that point. The default is now `8000` and overridable via `PROJECTMIND_IMPORT_GRAPH_MAX_FILES`.
- **Truncation is now logged** — when the file cap is hit, `build_import_graph` emits a `WARNING` telling you edges are missing and how to raise the limit, instead of failing silently.
- `config.get_import_graph_max_files()` is the single source of truth; `build_import_graph` / `_build_import_graph_uncached` default to it when `max_files` is None.
- Tests: `tests/test_import_graph_cap.py` covering env override, invalid/non-positive fallback, the cap, config default, and truncation warning.

### Why
- Impact analysis, relations, clustering, and dependency-path search all read this graph. On big repos the silent 3000-file ceiling produced blind spots that degraded context curation; the cap is now visible and tunable.

---

## [0.7.5] - 2026-06-08 🔎 FULL-FILE L0 SYMBOLS

### Fixed
- **L0 manifest symbol extraction** — symbols were only scanned in the first 200 lines of each file, so classes/functions defined lower in a module were invisible to the `overview` tier and to `query()`'s L0 layer. Extraction now runs over the whole file (still bounded by the existing 256 KB call-site guard and the `MAX_SYMBOLS_PER_FILE` cap), so deep-in-file definitions are surfaced.
- Tests: `tests/test_manifest_symbols.py` covering symbols past line 200, dedup order, per-language regexes, and the cap.

### Why
- The cheap-first `overview`/L0 path is what cheap models hit first. Missing symbols meant the model never learned that a relevant function even existed, forcing needless escalation or hallucinated APIs.

---

## [0.7.4] - 2026-06-08 🧠 RELEVANCE-RANKED MEMORY

### Added
- **`search_memory(query, n_results)` tool** — relevance-ranked retrieval over `memory.md`. Splits memory into logical blocks (`## ` sections and `### ` sub-entries) and returns the top-k blocks scored by keyword overlap, instead of returning only the head of the file (the old `read_memory` truncated to the first 100 lines).
- **`MemoryManager.search_blocks`** — pure, unit-tested scoring core (heading matches weighted higher than body matches, plus a query-coverage bonus).
- Tests: `tests/test_memory_search.py` covering tokenization, block splitting, scoring, ranking, k-limit, and empty/missing-file cases.

### Why
- As memory grows, the most relevant decisions/conventions sank below the 100-line read window and became invisible to the model. Targeted retrieval keeps long-lived project knowledge usable without dumping the whole file into context.

---

## [0.7.3] - 2026-06-08 ⚖️ SCALE-INVARIANT TIER FUSION

### Fixed
- **`query()` cross-tier ranking** — L0/L1/L2 hits were merged by raw score, but the tiers use incompatible scales (L0 was a flat `1.0`, L1 a raw unbounded BM25 score, L2 a `0..1` similarity). The flat L0 score routinely outranked more relevant L1/L2 hits, and raw BM25 magnitudes distorted ordering.

### Changed
- **`_merge_hits` now uses Reciprocal Rank Fusion (RRF)** — ranking depends only on each item's position *within its own tier*, so it is scale-invariant and rewards results corroborated across tiers. The fused value is normalized to `0..1` (1.0 = top-ranked in every contributing tier) so the L2-escalation confidence threshold stays meaningful.
- Tests: `tests/test_query_router_fusion.py` covering empty/single-bucket, cross-tier corroboration, BM25-magnitude invariance, 0..1 bounds, and representative selection.

### Why
- `query()` is the primary context-curation entry point. Correct fusion means cheaper models receive the most relevant snippets first instead of whichever tier happened to emit the largest raw number.

---

## [0.7.2] - 2026-06-08 🕸️ MONOREPO-AWARE IMPORT GRAPH

### Added
- **JS/TS import resolution for `tsconfig`/`jsconfig` path aliases** (e.g. `@/...`, `@app/...`) — the import graph now maps aliased specifiers to real files instead of silently dropping them.
- **Workspace / monorepo package resolution** — bare imports of local packages (matched via each `package.json` `"name"`, honoring `module`/`main`/`types` entry points and subpaths) are now edges in the dependency graph.
- **JSONC-tolerant config parser** — `tsconfig`/`jsconfig` files containing comments and trailing commas are parsed correctly.
- Tests: `tests/test_import_graph.py` covering relative, alias, workspace, subpath, JSONC, and full-graph resolution.

### Why
- `analyze_change_impact`, `get_file_relations`, `get_module_cluster`, and `find_dependency_path` previously saw only relative imports, so monorepos and alias-based projects had large blind spots. Cross-package and aliased edges are now visible, improving context-curation quality.

---

## [0.7.1] - 2026-03-13 🛡️ INDEX PREREQUISITE GUARD

### Added
- **`_check_index_ready()`** internal guard function — validates that the vector store index is built and non-empty before any search tool runs
  - Checks for DB file existence via SQLite (no vector store initialization cost)
  - Distinguishes between: index not built, index empty, index corrupted
  - Returns actionable step-by-step instructions for the AI to follow

### Changed
- **Search tools now fail fast with clear instructions** if `index_codebase()` was not called first:
  - `search_codebase`
  - `search_codebase_advanced`
  - `search_with_dependencies`
  - `search_for_errors`
  - `search_for_feature`
  - `search_architecture`

---

## [0.7.0] - 2026-02-17 🧠 GRAPH-ENHANCED SEARCH & INTELLIGENCE

### Added - Graph-Based Tools (4 new tools)
- **`get_dependencies_with_depth(file_path, depth, direction)`** - Traverse dependency graph up to 5 levels deep
  - Supports both downstream (what it imports) and upstream (what imports it)
  - Groups results by distance from origin file
  - BFS traversal for accurate depth tracking

- **`find_dependency_path(from_file, to_file, max_depth)`** - Find shortest dependency chain between files
  - Useful for understanding how modules are connected
  - Shows step-by-step import path
  - Configurable search depth (1-20 levels)

- **`get_module_cluster(file_path, similarity_threshold, max_cluster_size)`** - Find related modules
  - Uses Jaccard similarity on shared dependencies
  - Identifies files that work together
  - Configurable similarity threshold (0.0-1.0)

- **`search_with_dependencies(query, n_results, include_deps, depth)`** - Hybrid search
  - Combines semantic search with dependency graph
  - Automatically includes related files
  - Provides complete context for code understanding

### Added - Specialized Search Tools (3 new tools)
- **`search_for_errors(error_text, stacktrace, n_results)`** - Debug-focused search
  - Searches error handlers, tests, similar patterns
  - Includes recent git commits mentioning the error
  - Organized output for efficient debugging

- **`search_for_feature(feature_name, n_results)`** - Feature understanding
  - Finds implementations, configs, tests, docs
  - Identifies entry points automatically
  - Shows feature structure and organization

- **`search_architecture(component, n_results)`** - Architectural analysis
  - Finds core modules and dependencies
  - Shows module clustering and relationships
  - Helps understand system design

### Enhanced - Search Results Metadata
- **`search_codebase()`** now includes:
  - Confidence scores (0-100%)
  - Coverage indicators (full/partial)
  - Per-result relevance scores
  - Smart suggestions based on results quality
  - File count and result statistics

### Technical Improvements
- **Graph Intelligence**: 3 new graph utility functions in `code_intelligence.py`
  - `get_dependencies_with_depth()` - BFS traversal with configurable depth
  - `find_dependency_path()` - Shortest path algorithm
  - `get_module_cluster()` - Jaccard similarity clustering
- **Performance**: All graph operations use cached import graph
- **Accuracy**: Dependency resolution improved for Python, JS/TS files
- **Code Quality**: Type hints, validation, error handling for all new tools

### Total Tools
- **v0.6.0**: 29 tools
- **v0.7.0**: 36 tools (+7 new)

## [0.6.1] - 2026-02-17 🚀 CRITICAL PERFORMANCE FIX

### Fixed
- **Critical: Fixed 10-minute hangs in memory operations**
  - `read_memory()` now uses direct file access (>1000x faster)
  - All memory operations (update, clear, delete, versions) bypass VectorStore initialization
  - `get_index_stats()` pre-checks DB existence before context creation
  - Memory operations are now instant and non-blocking

### Changed
- **Memory operations no longer trigger VectorStore initialization**
  - `read_memory()`, `update_memory()`, `clear_memory()`, `delete_memory_section()`
  - `save_memory_version()`, `list_memory_versions()`, `restore_memory_version()`
  - `get_project_memory()` resource
  - All use direct file access or lightweight MemoryManager instances

### Performance Impact
- Memory operations: 0-60s → <10ms (parallel call scenarios)
- Eliminates blocking when AI calls multiple tools in parallel
- VectorStore only initializes when actually needed (search/index operations)

### Documentation
- Added `PERFORMANCE_FIX.md` with detailed analysis and recommendations
- Updated tool descriptions to reflect performance characteristics

## [0.6.0] - 2026-02-15 🧠 CODE INTELLIGENCE & ADVANCED ANALYSIS

### Added
- **Code Intelligence Module** (`code_intelligence.py`)
  - **`detect_project_conventions()`** - Auto-detects naming style, test patterns, frameworks, linting/formatting tools, error handling, logging, and architecture from codebase
  - **`get_file_relations(path)`** - Shows import relationships (what file imports, what imports it), related tests, and impact assessment
  - **`find_todos(tag=None)`** - Scans codebase for TODO, FIXME, HACK, BUG, XXX comments with file locations and line numbers

- **Dependency Analysis**
  - **`check_dependencies()`** - Analyzes dependency health across Python (pyproject.toml, requirements.txt), JavaScript (package.json), Go (go.mod), and Rust (Cargo.toml)
  - Reports total dependencies, version pinning strategies, duplicates, and lock file status
  - Detects caret (^), tilde (~), and exact version constraints

- **Change Impact Analysis**
  - **`analyze_change_impact(path)`** - Predicts what breaks if you change a file
  - Uses import graph to find direct and transitive dependents
  - Identifies related tests to run
  - Provides risk assessment (MINIMAL/LOW/MEDIUM/HIGH/CRITICAL)

- **Memory Integration**
  - **`save_conventions_to_memory()`** - Detects conventions and auto-saves to memory.md
  - **`project_onboarding()`** - One-command full project briefing combining overview + conventions + dependencies + TODOs
  - Auto-saves conventions and dependencies to memory for persistent context

### Technical Details
- All new tools use static analysis (no ML/vector store required)
- Import graph built with BFS for transitive dependency tracking
- Supports Python, JavaScript/TypeScript, Go, and Rust ecosystems
- ~940 lines of new code in `code_intelligence.py`
- Total tools increased from 22 to 29
- All tests passing, black formatting compliant

## [0.5.6] - 2026-02-09 🛠️ STABILITY & PERFORMANCE FIXES

### Fixed
- **Critical: Fixed server hang on large/external projects**
  - Added safety limit to `scan_indexable_files` (stops after 20,000 files)
  - Prevents infinite hangs on massive mono-repositories or recursive directory structures
  - Server now stays responsive even when opened in root directories

- **Enhanced Default Ignore List**
  - Added `target`, `vendor`, `bin`, `obj`, `out`, `logs`, `tmp`, `temp`, `.cache`, `.gradle` to default ignore list
  - Prevents indexing of massive build artifacts and dependency folders (Rust, Go, Java, C#, etc.)
  - Significantly improves indexing speed and reduces memory usage

### Added
- **Regression Test for Indexing Limits**
  - Added `tests/test_indexing_limit.py` to verify file scanning limits
  - Ensures the 20,000 file limit is respected

### Technical Details
- Expanded `DEFAULT_IGNORED_DIRS` in `config.py`
- Added `max_files` parameter to `CodebaseIndexer.scan_indexable_files`
- Verified fixes with new unit tests and `black` formatting

## [0.5.5] - 2026-02-05 🔧 CRITICAL BUGFIXES & IMPROVEMENTS

### Fixed
- **Critical: Fixed numpy version incompatibility**
  - Changed constraint from `numpy<2.0` to `numpy>=1.24.0,<2.1.0`
  - Resolves compatibility issues with chromadb and sentence-transformers
  - Package now correctly installed as version 0.5.5 (was stuck at 0.4.0)

- **Critical: Fixed exception name shadowing**
  - Renamed `IndexError` → `CodebaseIndexError`
  - Renamed `MemoryError` → `MemoryOperationError`
  - Prevents conflicts with Python built-in exceptions

- **Thread-safety improvements**
  - Added `threading.Lock` for global cache in `analyze_project_structure()`
  - Prevents race conditions in concurrent cache access

- **Fixed relative path usage**
  - `extract_tech_stack()` now uses `PROJECT_ROOT`-based absolute paths
  - Ensures correct file detection regardless of working directory

### Added
- **MCP Configuration Template**
  - Added `.zencoder/mcp.local.json` configuration
  - Server now properly recognized by MCP clients
  - Includes full path to Python executable and server script

- **Development tools**
  - Installed mypy for type checking
  - All linting issues resolved (ruff clean)

### Technical Details
- All 131 tests passing with 78% coverage
- Zero ruff linting errors
- Server startup verified
- Compatible with numpy 2.0.2

## [0.5.4] - 2026-01-31 🧠 INTELLIGENT PROJECT AUTO-DETECTION

### Added
- **Automatic Project Detection** - No manual configuration needed!
  - Automatically finds project root using multiple strategies
  - Searches for project markers (.git, package.json, pyproject.toml, etc.)
  - Checks environment variables (WORKSPACE_FOLDER, PROJECT_ROOT, PROJECT_PATH)
  - Supports --project-root command-line argument
  - Works seamlessly across different IDEs and editors
  
### Changed
- **Enhanced Startup Logging**
  - Shows detected project root, cwd, and server location
  - Helps diagnose project detection issues
  - Clear separation in logs for better readability

### Technical Details
- Added `find_project_root()` function with 4-tier detection strategy
- Searches up to 10 directory levels for project markers
- Supports 10+ project types (Node.js, Python, Rust, Go, Java, etc.)
- Falls back to current working directory if no markers found
- All 131 tests passing

## [0.5.3] - 2026-01-31 🎯 PROJECT ROOT & MEMORY PAGINATION FIX

### Fixed
- **Critical: PROJECT_ROOT now uses current working directory (cwd)**
  - Fixed server working on itself instead of target project
  - MCP server now correctly detects the project directory where it's invoked
  - Allows single server installation to work with multiple projects
  
### Added
- **Memory Pagination to Reduce Context Usage**
  - `read_memory()` now accepts `max_lines` parameter (default: 100)
  - Prevents overwhelming context windows with large memory files
  - Shows truncation message with remaining line count
  - Use `read_memory(max_lines=None)` for full content

### Changed
- **PROJECT_ROOT Detection**
  - Changed from `Path(__file__).parent` to `Path.cwd()`
  - Server now works with any project when cwd is set correctly
  - IDE/client must set working directory to target project

### Technical Details
- Updated `config.py` PROJECT_ROOT to use `Path.cwd()`
- Added `max_lines` parameter to `MemoryManager.read()`
- Added validation for `max_lines` in `read_memory()` tool
- All 131 tests pass successfully

## [0.5.2] - 2026-01-31 🚀 LAZY INITIALIZATION FIX

### Fixed
- **Critical: Eliminated Unnecessary Vector Store Initialization**
  - Fixed `get_index_stats()` triggering 30-60 second model loading
  - Fixed server freezing on parallel `read_memory()` + `get_index_stats()` calls
  - `VectorStoreManager.get_count()` no longer forces initialization
  - Added explicit initialization check before accessing collection

### Performance Improvements
- **Lazy Loading for Vector Store**
  - Vector store only initializes when actually needed (indexing/search)
  - `read_memory()` and `get_index_stats()` return instantly if not initialized
  - SentenceTransformer model (80MB) loads only on first index/search operation
  
### Technical Details
- Modified `VectorStoreManager.get_count()` to check `_initialized` flag
- Added early return in `get_index_stats()` before accessing collection
- Added diagnostic logging for initialization timing
- All functionality remains unchanged, only initialization is optimized

## [0.5.1] - 2026-01-30 ⚡ PERFORMANCE OPTIMIZATION

### Fixed
- **Critical Performance Issues**
  - Fixed server freezing when reading memory or executing analysis tools
  - Eliminated blocking recursive file system operations
  - Resolved slow response times on large codebases

### Performance Improvements
- **Added TTL Caching for `analyze_project_structure()`**
  - 5-minute cache (configurable via `STRUCTURE_CACHE_TTL`)
  - Prevents redundant file system scans
  - Returns cached results instantly for repeated requests

- **Optimized `generate_project_summary()`**
  - Replaced 3 separate `rglob()` calls with single `os.walk()` traversal
  - 3x faster execution on medium-sized projects
  - Proper directory filtering (ignores `.git`, `node_modules`, `.venv`, etc.)

- **Enhanced Directory Traversal**
  - Added error handling for `PermissionError` and `OSError`
  - Skips inaccessible directories instead of crashing
  - Counts only files (not directories) for accurate statistics

### Technical Details
- Moved all imports to module top for proper organization
- Added global cache variables with TTL tracking
- Improved `analyze_project_structure()` from O(n²) to O(n) complexity
- All 11 MCP tool tests pass successfully

## [0.5.0] - 2026-01-29 🏗️ DEPENDENCY INJECTION & CODE QUALITY

### Added
- **Custom Exception Hierarchy** (`exceptions.py`)
  - `ProjectMindError` base class for all exceptions
  - Specific exceptions: `IndexError`, `SearchError`, `MemoryError`, `ConfigError`, `VectorStoreError`, `GitError`, `ValidationError`
  - Better error handling and debugging

- **Git Utilities Module** (`git_utils.py`)
  - `CommitInfo` dataclass with formatted properties (`first_line`, `date_str`, `date_short`)
  - `GitRepository` class for git operations
  - Eliminated code duplication across 3+ MCP tools
  - Methods: `get_commits()`, `get_commits_by_author()`, `get_author_stats()`, `format_commits_summary()`, `format_author_stats()`

- **Dependency Injection Context** (`context.py`)
  - `AppContext` dataclass replacing global singletons
  - Functions: `get_context()`, `set_context()`, `reset_context()`
  - Improved testability and modularity
  - Eliminates global state issues

- **Structured Logging** (`logger.py`)
  - `StructuredFormatter` class for JSON extra fields
  - Enhanced log output with context information

### Changed
- **Refactored `codebase_indexer.py`**
  - Added `_create_batch_upsert_callback()` method
  - Eliminated duplicated callback code
  - Added proper type hints with `Callable`

- **Refactored `mcp_server.py`**
  - All MCP tools use `get_context()` instead of globals
  - Replaced direct `git.Repo` usage with `GitRepository` class
  - Updated all git-related functions: `ingest_git_history()`, `generate_project_summary()`, `get_recent_changes_summary()`, `auto_update_memory_from_commits()`

### Fixed
- **Test Suite Improvements**
  - Rewrote `test_search.py` with 14 comprehensive test cases
  - Created `test_git_utils.py` with 11 tests
  - Created `test_context.py` with 7 tests
  - Fixed `test_get_stats_empty` - added proper mocking for metadata file
  - Fixed `test_logger_setup` - corrected logger name expectation
  - Fixed `test_rag_tools` - handles numpy 2.0 incompatibility gracefully
  - Fixed `test_mcp_server` - Windows path compatibility (`.venv\Scripts\python.exe`)
  - **Test Pass Rate: 131/131 (100%)**

### Technical Details
- Updated `pyproject.toml` with new modules
- All tests passing on Windows platform
- Improved code coverage to 78%
- Better separation of concerns and modularity

## [0.4.1] - 2026-01-27 🐛 CRITICAL BUG FIXES

### Fixed
- **Missing `os` import** causing immediate server crash
  - Server was using `os.getcwd()` in 5 locations without importing the `os` module
  - Caused `NameError` crash during `startup_check()` at module load time
  - Prevented MCP server from starting in any IDE integration
  - Fixed by adding `import os` to mcp_server.py imports

### Documentation
- **Added Zencoder.ai IDE integration guide** to README
  - Complete configuration examples for Windows/macOS/Linux
  - Proper `stdio` type configuration format
  - Working examples with absolute paths

## [0.4.0] - 2026-01-26 🎯 PRODUCTION-READY REFACTORING

### 🏗️ Complete Architecture Overhaul (Stages 1-10)

Comprehensive refactoring focused on security, reliability, performance, and maintainability. All changes validated with 62 unit tests.

#### Stage 1-2: Security & Reliability Foundation
- **Path Validation Security**
  - `validate_path()` prevents directory traversal attacks
  - All file operations protected against path injection
  - Validates paths are within project root
  
- **Enhanced Unicode Handling**
  - `safe_read_text()` with multi-encoding support
  - Tries UTF-8, UTF-8-sig, Latin-1, CP1252, ISO-8859-1
  - Graceful fallback instead of silent errors
  - Proper error reporting for undecodable files

#### Stage 3: Centralized Logging
- **Professional Logging System**
  - Rotating file handler (10MB per file, 5 backups)
  - Logs stored in `.ai/projectmind.log`
  - Configurable levels and formats
  - Thread-safe implementation
  - Imported from `logger.py` throughout codebase

#### Stage 4: Transactional Index Saving
- **Atomic File Operations**
  - `atomic_write()` with temp file + rename pattern
  - Cross-platform file locking (fcntl/msvcrt)
  - Prevents corrupted metadata files
  - Guaranteed write atomicity
  - Crash-safe index persistence

#### Stage 5: Memory-Limited Indexing
- **OOM Prevention**
  - `MemoryLimitedIndexer` class with automatic batching
  - Configurable memory limits (default 100MB)
  - Auto-flush when threshold reached
  - Memory estimation for documents
  - Prevents system crashes on large codebases

#### Stage 6: CI/CD Enhancements
- **Enhanced GitHub Actions Pipeline**
  - Tests across Python 3.10, 3.11, 3.12
  - 7 parallel test suites
  - Black formatting validation
  - Ruff linting checks
  - MyPy type checking
  - Bandit security scanning
  - YAML/JSON/TOML validation

#### Stage 7: Comprehensive Unit Testing
- **Test Coverage with Mocks**
  - 45 unit tests across 3 test files
  - `tests/test_config.py` (17 tests) - Path validation, encoding
  - `tests/test_incremental_indexing.py` (14 tests) - Atomic writes, metadata
  - `tests/test_memory_limited_indexer.py` (14 tests) - Memory management
  - Full isolation using `unittest.mock`
  - All tests passing successfully

#### Stage 8: Function Refactoring
- **Code Complexity Reduction**
  - Extracted helper functions from monolithic code
  - `scan_indexable_files()` - Directory traversal
  - `process_file_to_chunks()` - File processing
  - `process_file_with_metadata()` - File + metadata updates
  - `should_include_search_result()` - Result filtering
  - `format_search_result()` - Result formatting
  - Reduced function length by 40-50%
  - Improved testability and reusability

#### Stage 9: Class-Based Architecture
- **Zero Global State**
  - Created `VectorStoreManager` class (178 lines)
    - Manages ChromaDB client and collection
    - Lazy initialization pattern
    - Thread-safe operations
  - Created `MemoryManager` class (224 lines)
    - Encapsulates all memory operations
    - Version management
    - Section manipulation
  - Created `CodebaseIndexer` class (243 lines)
    - File scanning and filtering
    - Chunking and indexing logic
    - Both full and incremental indexing
  - Eliminated 3 global variables
  - Removed 130+ lines of duplicated code
  - Better separation of concerns

#### Stage 10: Performance Caching 🆕
- **Multi-Layer Caching System**
  - `LRUCache` - Least Recently Used eviction
    - Configurable capacity
    - Automatic eviction
    - Hit/miss tracking
    - Thread-safe
  - `TTLCache` - Time-To-Live expiration
    - Configurable TTL (default 5 minutes)
    - Automatic cleanup
    - Expiration tracking
    - Thread-safe
  - `FileCache` - Specialized file content cache
    - Built on LRUCache
    - Modification time tracking
    - Auto-invalidation on file changes
    - 50 files capacity

- **Cache Integration**
  - `config.safe_read_text()` uses FileCache
  - `VectorStoreManager.query()` uses TTLCache (300s TTL)
  - Lazy initialization to avoid circular imports
  - SHA256-based cache keys for queries

- **Monitoring Tool** 🆕
  - `get_cache_stats()` - Cache performance metrics
  - Reports hits, misses, hit rates
  - Separate stats for file and query caches
  - Size and capacity tracking

### 📦 New Files Created
- `logger.py` - Centralized logging system
- `cache_manager.py` - LRUCache, TTLCache, FileCache implementations
- `vector_store_manager.py` - ChromaDB management class
- `memory_manager.py` - Memory operations class
- `codebase_indexer.py` - Indexing operations class
- `tests/test_config.py` - Configuration validation tests
- `tests/test_incremental_indexing.py` - Atomic write tests
- `tests/test_memory_limited_indexer.py` - Memory management tests
- `tests/test_cache_manager.py` - Cache functionality tests (17 tests)

### 🔧 Files Modified
- `config.py` - Added path validation, Unicode handling, file caching
- `mcp_server.py` - Migrated to class-based architecture, added cache stats tool
- `incremental_indexing.py` - Added atomic write operations

### ✅ Testing Results
- **62 unit tests** passing (45 existing + 17 new cache tests)
- Zero test failures
- Full coverage of critical paths
- Mock-based isolation for reliability

### 🚀 Performance Improvements
- **Caching Benefits:**
  - Reduced disk I/O for repeated file reads
  - 5-minute query result caching for faster searches
  - Thread-safe for concurrent access
  - Transparent monitoring via stats

- **Memory Safety:**
  - Prevents OOM crashes on large codebases
  - Automatic batching with memory limits
  - Configurable thresholds

- **Reliability:**
  - Atomic file operations prevent corruption
  - Cross-platform file locking
  - Crash-safe metadata persistence

### 📊 Code Quality Metrics
- **Lines of Production Code Added:** ~1,300 lines (5 new classes)
- **Lines Removed/Refactored:** ~150 lines (eliminated duplication)
- **Test Coverage:** 62 comprehensive unit tests
- **Global Variables Eliminated:** 3 (chroma_client, collection, embedding_fn)
- **Complexity Reduction:** 40-50% in main functions

### 🔒 Security Enhancements
- Path traversal prevention
- Input validation on all file operations
- Secure file handling with proper error reporting
- No exposed secrets or credentials

### 🏛️ Architecture Improvements
- **Before:** Monolithic functions, global state, no caching
- **After:** Class-based architecture, zero globals, multi-layer caching
- Better testability through dependency injection
- Clear separation of concerns
- Easier to maintain and extend

### 📝 Commits
- `f4a6436` - feat: Add comprehensive unit tests with mocks
- `22cd6d0` - refactor: Break down large functions into smaller units
- `4bf726c` - refactor: Migrate to class-based architecture
- `4b291dd` - feat: Add comprehensive caching layer (Stage 10)

### 🎓 Technical Decisions
- Chose class-based architecture over modules for better encapsulation
- Implemented lazy initialization to avoid circular imports
- Used SHA256 hashing for cache keys to ensure uniqueness
- Selected LRU and TTL strategies based on use cases
- Maintained backward compatibility throughout refactoring

---

## [0.3.0] - 2025-12-16 🚀 MAJOR UPDATE

### 🎉 5 Major New Features

#### 1️⃣ Incremental Indexing
- **`index_changed_files()`** - Only re-indexes modified files
- 10-100x faster for large codebases
- Automatic file modification tracking
- Smart deletion of removed files
- Metadata storage in `.ai/index_metadata.json`

#### 2️⃣ Advanced Search Filters
- **`search_codebase_advanced()`** with powerful filters:
  - Filter by file types (`.py`, `.js`, etc.)
  - Exclude specific directories
  - Minimum relevance threshold (0-1)
  - Relevance scores in search results
- Precise search in specific parts of codebase

#### 3️⃣ Automatic Memory Updates
- **`auto_update_memory_from_commits()`** - Smart git integration
- Auto-summarization of commits (when > 5)
- Groups changes by contributors
- Highlights key changes
- Configurable time period (1-90 days)

#### 4️⃣ Code Quality & Metrics
- **`analyze_code_complexity()`** - Cyclomatic complexity analysis
  - Identifies high-complexity functions (>10)
  - Average complexity calculation
  - Python support
- **`analyze_code_quality()`** - Pylint integration
  - Errors, warnings, refactoring suggestions
  - Convention issues tracking
  - Quality scoring
- **`get_test_coverage_info()`** - Coverage tracking
  - Parses `.coverage` and `htmlcov/`
  - Overall coverage percentage
  - Links to detailed reports

#### 5️⃣ Memory Versioning
- **`save_memory_version()`** - Create memory snapshots
- **`list_memory_versions()`** - View version history
- **`restore_memory_version()`** - Rollback to previous state
- Git-like versioning for memory.md
- Auto-backup before restore
- Stored in `.ai/memory_history/`

### 📦 New Dependencies
- `radon>=6.0.0` - Code complexity analysis
- `pylint>=3.0.0` - Code quality checks

### 📝 New Files
- `incremental_indexing.py` - Metadata management for incremental indexing
- `.ai/index_metadata.json` - File modification tracking
- `.ai/memory_history/` - Memory version storage

### 🔧 Infrastructure Changes
- Added `INDEX_METADATA_FILE` to config
- Added `MEMORY_HISTORY_DIR` to config
- New imports: `json`, `shutil`, `timedelta`
- Extended type hints with `Dict`

### ✅ Testing
- Added 5 new test suites
- Total test functions: 11
- Coverage for all new features

### 📚 Documentation
- Comprehensive README updates
- New sections for all 5 features
- Code examples for advanced features
- Updated Quick Start guide

---

## [0.2.0] - 2025-12-16

### 🎉 Major Improvements

#### Infrastructure
- ✅ Migrated from `requirements.txt` to modern `pyproject.toml`
- ✅ Added comprehensive `.gitignore` with Python-specific patterns
- ✅ Removed duplicate `venv/` directory
- ✅ Created centralized `config.py` for all configuration settings
- ✅ Added GitHub Actions CI/CD pipeline
- ✅ Configured pre-commit hooks for code quality

#### Code Quality
- ✅ Added comprehensive type hints throughout the codebase
- ✅ Implemented input validation for all tool parameters
- ✅ Enhanced error handling with proper exception management
- ✅ Improved code organization and structure
- ✅ Added security checks (bandit) to CI pipeline

#### New Features

**Memory Management:**
- ✅ `clear_memory(keep_template: bool)` - Clear memory with optional template preservation
- ✅ `delete_memory_section(section_name: str)` - Delete specific memory sections
- ✅ `get_index_stats()` - Get vector store statistics

**Smart Indexing:**
- ✅ File type filtering (50+ programming languages and text formats)
- ✅ File size limits (configurable, default 10MB)
- ✅ Custom ignore patterns via `.ai/.indexignore`
- ✅ Binary file detection and exclusion
- ✅ Improved file scanning performance

**Validation & Safety:**
- ✅ Query validation (non-empty, reasonable limits)
- ✅ Result count validation (1-50 range)
- ✅ Git history limit validation (1-1000 range)
- ✅ Empty content detection

#### Testing
- ✅ Expanded test suite with 5 test categories
- ✅ Fixed lazy loading issue in `test_search.py`
- ✅ Added validation tests
- ✅ Added memory management tests
- ✅ Added git integration tests
- ✅ Better error reporting with tracebacks

#### Documentation
- ✅ Completely rewritten README with detailed API documentation
- ✅ Added configuration guide
- ✅ Added troubleshooting section
- ✅ Added development and contribution guidelines
- ✅ Documented all new features and tools

### 🐛 Bug Fixes
- Fixed `test_search.py` attempting to import `collection` directly (lazy loading issue)
- Fixed missing error handling in indexing operations
- Fixed potential issues with empty file handling
- Fixed hardcoded configuration values

### 🔧 Configuration
- Configurable maximum file size via `PROJECTMIND_MAX_FILE_SIZE_MB` environment variable
- Centralized chunk size and overlap configuration
- Customizable ignored directories and file extensions
- Flexible batch size for indexing operations

### 📦 Dependencies
Added development dependencies:
- `pytest` & `pytest-cov` for testing
- `black` for code formatting
- `ruff` for linting
- `mypy` for type checking
- `pre-commit` for git hooks

### 🏗️ Architecture Changes
- Separated configuration into `config.py`
- Improved function signatures with type hints
- Better separation of concerns
- More maintainable and scalable codebase

---

## [0.1.0] - Initial Release

### Features
- Basic MCP server implementation
- Project memory management
- Git history ingestion
- Local RAG with ChromaDB
- Vector search functionality
- Auto-initialization of `.ai/` directory

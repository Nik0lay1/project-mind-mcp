# Changelog

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

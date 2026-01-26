import os
import sys
import json
import shutil
from pathlib import Path
from typing import List, Optional, Set, Dict
from mcp.server.fastmcp import FastMCP
import git
from datetime import datetime, timedelta

from config import (
    AI_DIR,
    MEMORY_FILE,
    VECTOR_STORE_DIR,
    INDEX_IGNORE_FILE,
    INDEX_METADATA_FILE,
    MEMORY_HISTORY_DIR,
    MODEL_NAME,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    BATCH_SIZE,
    BINARY_EXTENSIONS,
    INDEXABLE_EXTENSIONS,
    get_max_file_size_bytes,
    get_ignored_dirs,
    validate_path,
    safe_read_text,
)
from incremental_indexing import IndexMetadata

chroma_client = None
collection = None
embedding_fn = None


def log(message: str) -> None:
    sys.stderr.write(f"[ProjectMind] {message}\n")
    sys.stderr.flush()


def startup_check() -> None:
    log(f"Running startup check in {os.getcwd()}...")

    try:
        if not AI_DIR.exists():
            AI_DIR.mkdir(parents=True)
            log(f"Created {AI_DIR}")
    except (OSError, PermissionError) as e:
        log(f"Warning: Could not create {AI_DIR}: {e}. Server will continue if directory exists.")

    try:
        gitignore_path = Path(".gitignore")
        ai_ignored = False
        pycache_ignored = False

        if gitignore_path.exists():
            content = gitignore_path.read_text()
            if ".ai/" in content or ".ai" in content:
                ai_ignored = True
            if "__pycache__" in content:
                pycache_ignored = True

            if not ai_ignored or not pycache_ignored:
                with open(gitignore_path, "a") as f:
                    if not content.endswith("\n") and content:
                        f.write("\n")
                    if not ai_ignored:
                        f.write(".ai/\n")
                        log("Added .ai/ to .gitignore")
                    if not pycache_ignored:
                        f.write("__pycache__/\n")
                        log("Added __pycache__/ to .gitignore")
        else:
            with open(gitignore_path, "w") as f:
                f.write(".ai/\n__pycache__/\n")
            log("Created .gitignore with .ai/ and __pycache__/")
    except (OSError, PermissionError) as e:
        log(f"Warning: Could not modify .gitignore: {e}")

    try:
        if not MEMORY_FILE.exists():
            template = """# Project Memory

## Status
- [ ] Initial Setup

## Tech Stack
- Language: Python
- Framework: 

## Recent Decisions
- Project initialized.
"""
            MEMORY_FILE.write_text(template)
            log(f"Created {MEMORY_FILE}")
    except (OSError, PermissionError) as e:
        log(f"Warning: Could not create {MEMORY_FILE}: {e}")


startup_check()

mcp = FastMCP("ProjectMind")


def get_vector_store():
    global chroma_client, collection, embedding_fn

    if collection is not None:
        return collection

    log("Initializing Vector Store (this may take a few seconds)...")

    import chromadb
    from chromadb.utils import embedding_functions
    from sentence_transformers import SentenceTransformer

    class LocalSentenceTransformerEmbeddingFunction(embedding_functions.EmbeddingFunction):
        def __init__(self, model_name: str) -> None:
            self.model = SentenceTransformer(model_name)

        def __call__(self, input: List[str]) -> List[List[float]]:
            return self.model.encode(input).tolist()

    try:
        chroma_client = chromadb.PersistentClient(path=str(VECTOR_STORE_DIR))
        embedding_fn = LocalSentenceTransformerEmbeddingFunction(MODEL_NAME)
        collection = chroma_client.get_or_create_collection(
            name="project_codebase", embedding_function=embedding_fn
        )
        log("Vector Store initialized.")
        return collection
    except Exception as e:
        log(f"Error initializing ChromaDB: {e}")
        return None


def load_index_ignore_patterns() -> Set[str]:
    if not INDEX_IGNORE_FILE.exists():
        return set()

    try:
        patterns = set()
        with open(INDEX_IGNORE_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.add(line)
        return patterns
    except Exception as e:
        log(f"Error reading .indexignore: {e}")
        return set()


def should_index_file(file_path: Path, ignore_patterns: Set[str]) -> bool:
    if file_path.suffix in BINARY_EXTENSIONS:
        return False

    if file_path.suffix and file_path.suffix not in INDEXABLE_EXTENSIONS:
        return False

    file_str = str(file_path)
    for pattern in ignore_patterns:
        if pattern in file_str:
            return False

    try:
        file_size = file_path.stat().st_size
        if file_size > get_max_file_size_bytes():
            log(f"Skipping {file_path}: exceeds max file size")
            return False
    except Exception:
        return False

    return True


@mcp.resource("project://memory")
def get_project_memory() -> str:
    if MEMORY_FILE.exists():
        return MEMORY_FILE.read_text()
    return "Memory file not found."


@mcp.tool()
def read_memory() -> str:
    if MEMORY_FILE.exists():
        return MEMORY_FILE.read_text()
    return "Memory file not found."


@mcp.tool()
def update_memory(content: str, section: str = "Recent Decisions") -> str:
    if not MEMORY_FILE.exists():
        return "Memory file not found."

    if not content or not content.strip():
        return "Error: Content cannot be empty."

    current_content = MEMORY_FILE.read_text()

    new_entry = f"\n\n### Update ({section})\n{content}"

    with open(MEMORY_FILE, "a") as f:
        f.write(new_entry)

    return "Memory updated successfully."


@mcp.tool()
def clear_memory(keep_template: bool = True) -> str:
    if not MEMORY_FILE.exists():
        return "Memory file not found."

    try:
        if keep_template:
            template = """# Project Memory

## Status
- [ ] Initial Setup

## Tech Stack
- Language: Python
- Framework: 

## Recent Decisions
- Memory cleared.
"""
            MEMORY_FILE.write_text(template)
            return "Memory cleared (template preserved)."
        else:
            MEMORY_FILE.write_text("")
            return "Memory completely cleared."
    except Exception as e:
        return f"Error clearing memory: {e}"


@mcp.tool()
def delete_memory_section(section_name: str) -> str:
    if not MEMORY_FILE.exists():
        return "Memory file not found."

    if not section_name or not section_name.strip():
        return "Error: Section name cannot be empty."

    try:
        content = MEMORY_FILE.read_text()
        lines = content.split("\n")
        new_lines = []
        skip = False

        for line in lines:
            if line.startswith("##") and section_name.lower() in line.lower():
                skip = True
                continue
            elif line.startswith("##") and skip:
                skip = False

            if not skip:
                new_lines.append(line)

        MEMORY_FILE.write_text("\n".join(new_lines))
        return f"Section '{section_name}' deleted successfully."
    except Exception as e:
        return f"Error deleting section: {e}"


@mcp.tool()
def index_codebase(force: bool = False) -> str:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    coll = get_vector_store()
    if coll is None:
        return "Failed to initialize vector store."

    if force:
        log("Clearing existing index...")
        global chroma_client, collection, embedding_fn
        if chroma_client:
            try:
                chroma_client.delete_collection("project_codebase")
                collection = chroma_client.get_or_create_collection(
                    name="project_codebase", embedding_function=embedding_fn
                )
                coll = collection
            except Exception as e:
                log(f"Error clearing collection: {e}")
                return f"Error clearing collection: {e}"

    root_dir = Path(".")
    ignored_dirs = get_ignored_dirs()
    ignore_patterns = load_index_ignore_patterns()

    documents = []
    metadatas = []
    ids = []

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )

    log("Scanning files...")
    file_count = 0
    chunk_count = 0

    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]

        for file in files:
            file_path = Path(root) / file

            if not should_index_file(file_path, ignore_patterns):
                continue

            try:
                content = safe_read_text(file_path)
                if not content.strip():
                    continue

                chunks = text_splitter.split_text(content)

                for i, chunk in enumerate(chunks):
                    documents.append(chunk)
                    metadatas.append({"source": str(file_path), "chunk_index": i})
                    ids.append(f"{file_path}_{i}")

                file_count += 1
                chunk_count += len(chunks)

            except (UnicodeDecodeError, IOError) as e:
                log(f"Skipping {file_path}: {e}")
            except Exception as e:
                log(f"Skipping {file_path}: {e}")

    if documents:
        log(f"Indexing {len(documents)} chunks from {file_count} files...")
        try:
            for i in range(0, len(documents), BATCH_SIZE):
                end = min(i + BATCH_SIZE, len(documents))
                collection.upsert(
                    documents=documents[i:end], metadatas=metadatas[i:end], ids=ids[i:end]
                )
            return f"Indexed {file_count} files ({chunk_count} chunks)."
        except Exception as e:
            log(f"Error during indexing: {e}")
            return f"Error during indexing: {e}"
    else:
        return "No documents found to index."


@mcp.tool()
def search_codebase(query: str, n_results: int = 5) -> str:
    if not query or not query.strip():
        return "Error: Query cannot be empty."

    if n_results <= 0:
        return "Error: n_results must be greater than 0."

    if n_results > 50:
        return "Error: n_results cannot exceed 50."

    coll = get_vector_store()
    if coll is None:
        return "Vector store not initialized."

    try:
        results = coll.query(query_texts=[query], n_results=n_results)

        output = []
        if results["documents"]:
            for i in range(len(results["documents"][0])):
                doc = results["documents"][0][i]
                meta = results["metadatas"][0][i]
                source = meta.get("source", "unknown")
                output.append(f"--- {source} ---\n{doc}\n")

        return "\n".join(output) if output else "No matches found."
    except Exception as e:
        log(f"Search error: {e}")
        return f"Error during search: {e}"


@mcp.tool()
def ingest_git_history(limit: int = 30) -> str:
    if limit <= 0:
        return "Error: Limit must be greater than 0."

    if limit > 1000:
        return "Error: Limit cannot exceed 1000."

    try:
        repo = git.Repo(os.getcwd(), search_parent_directories=True)
    except git.InvalidGitRepositoryError:
        return "Current directory is not a git repository."
    except Exception as e:
        return f"Error accessing git repository: {e}"

    if not MEMORY_FILE.exists():
        return "Memory file not found."

    try:
        current_memory = MEMORY_FILE.read_text()

        header = "## Development Log (Git)"
        if header not in current_memory:
            with open(MEMORY_FILE, "a") as f:
                f.write(f"\n\n{header}\n")
            current_memory += f"\n\n{header}\n"

        new_entries = []

        for commit in repo.iter_commits(max_count=limit):
            short_hash = commit.hexsha[:7]

            if short_hash in current_memory:
                continue

            date_str = datetime.fromtimestamp(commit.committed_date).strftime("%Y-%m-%d %H:%M")
            message = commit.message.strip().replace("\n", " ")
            author = commit.author.name

            entry = f"- **{date_str}** [{short_hash}]: {message} (*{author}*)"
            new_entries.append(entry)

        if not new_entries:
            return "No new commits found to ingest."

        new_entries.reverse()

        with open(MEMORY_FILE, "a") as f:
            for entry in new_entries:
                f.write(f"{entry}\n")

        return f"Ingested {len(new_entries)} new commits into memory."
    except Exception as e:
        log(f"Error ingesting git history: {e}")
        return f"Error ingesting git history: {e}"


@mcp.tool()
def get_index_stats() -> str:
    coll = get_vector_store()
    if coll is None:
        return "Vector store not initialized."

    try:
        count = coll.count()
        return f"Vector store contains {count} chunks."
    except Exception as e:
        return f"Error getting stats: {e}"


@mcp.tool()
def generate_project_summary() -> str:
    try:
        summary_parts = []
        
        summary_parts.append("# PROJECT SUMMARY\n")
        
        memory = read_memory()
        if memory and "Memory file not found" not in memory:
            summary_parts.append("## Current Memory State")
            lines = memory.split('\n')[:30]
            summary_parts.append('\n'.join(lines))
            if len(memory.split('\n')) > 30:
                summary_parts.append("\n... (truncated, see full memory)\n")
        
        try:
            repo = git.Repo(os.getcwd(), search_parent_directories=True)
            commits = list(repo.iter_commits(max_count=5))
            if commits:
                summary_parts.append("\n## Recent Activity (Last 5 Commits)")
                for commit in commits:
                    date_str = datetime.fromtimestamp(commit.committed_date).strftime('%Y-%m-%d')
                    message = commit.message.strip().split('\n')[0][:80]
                    summary_parts.append(f"- {date_str}: {message}")
        except Exception:
            pass
        
        root = Path(".")
        py_files = len(list(root.rglob("*.py")))
        js_files = len(list(root.rglob("*.js"))) + len(list(root.rglob("*.ts")))
        
        summary_parts.append("\n## Codebase Stats")
        summary_parts.append(f"- Python files: {py_files}")
        summary_parts.append(f"- JavaScript/TypeScript files: {js_files}")
        
        stats = get_index_stats()
        summary_parts.append(f"- {stats}")
        
        return '\n'.join(summary_parts)
    except Exception as e:
        return f"Error generating summary: {e}"


@mcp.tool()
def extract_tech_stack() -> str:
    try:
        tech_stack = []
        
        if Path("pyproject.toml").exists():
            content = Path("pyproject.toml").read_text()
            tech_stack.append("## Python Project")
            if "dependencies" in content:
                tech_stack.append("\n**Dependencies:**")
                lines = content.split('\n')
                in_deps = False
                for line in lines:
                    if 'dependencies = [' in line:
                        in_deps = True
                        continue
                    if in_deps:
                        if ']' in line:
                            break
                        if '"' in line:
                            tech_stack.append(f"- {line.strip()}")
        
        elif Path("requirements.txt").exists():
            content = Path("requirements.txt").read_text()
            tech_stack.append("## Python Project")
            tech_stack.append("\n**Dependencies:**")
            for line in content.split('\n'):
                if line.strip() and not line.startswith('#'):
                    tech_stack.append(f"- {line.strip()}")
        
        if Path("package.json").exists():
            import json
            with open("package.json") as f:
                data = json.load(f)
            tech_stack.append("\n## JavaScript/Node.js Project")
            if "dependencies" in data:
                tech_stack.append("\n**Dependencies:**")
                for dep, ver in list(data["dependencies"].items())[:15]:
                    tech_stack.append(f"- {dep}: {ver}")
                if len(data["dependencies"]) > 15:
                    tech_stack.append(f"... and {len(data['dependencies']) - 15} more")
        
        if Path("Cargo.toml").exists():
            tech_stack.append("\n## Rust Project")
        
        if Path("go.mod").exists():
            tech_stack.append("\n## Go Project")
        
        if not tech_stack:
            return "No standard dependency files found (pyproject.toml, package.json, etc.)"
        
        return '\n'.join(tech_stack)
    except Exception as e:
        return f"Error extracting tech stack: {e}"


@mcp.tool()
def analyze_project_structure() -> str:
    try:
        root = Path(".")
        ignored_dirs = get_ignored_dirs()
        
        structure = []
        structure.append("# PROJECT STRUCTURE\n")
        
        dirs_by_depth = {}
        for item in root.iterdir():
            if item.is_dir() and item.name not in ignored_dirs:
                dirs_by_depth[item.name] = len(list(item.rglob("*")))
        
        sorted_dirs = sorted(dirs_by_depth.items(), key=lambda x: x[1], reverse=True)[:10]
        
        structure.append("## Main Directories (by size)")
        for dir_name, count in sorted_dirs:
            structure.append(f"- `{dir_name}/` ({count} items)")
        
        file_types = {}
        for ext in ['.py', '.js', '.ts', '.jsx', '.tsx', '.go', '.rs', '.java', '.c', '.cpp']:
            count = len(list(root.rglob(f"*{ext}")))
            if count > 0:
                file_types[ext] = count
        
        if file_types:
            structure.append("\n## File Types")
            for ext, count in sorted(file_types.items(), key=lambda x: x[1], reverse=True):
                structure.append(f"- `{ext}`: {count} files")
        
        config_files = []
        for cfg in ['pyproject.toml', 'package.json', 'Cargo.toml', 'go.mod', 
                    '.gitignore', 'docker-compose.yml', 'Dockerfile', '.env.example']:
            if Path(cfg).exists():
                config_files.append(cfg)
        
        if config_files:
            structure.append("\n## Configuration Files")
            for cfg in config_files:
                structure.append(f"- {cfg}")
        
        return '\n'.join(structure)
    except Exception as e:
        return f"Error analyzing structure: {e}"


@mcp.tool()
def get_recent_changes_summary(days: int = 7) -> str:
    if days <= 0 or days > 365:
        return "Error: days must be between 1 and 365"
    
    try:
        repo = git.Repo(os.getcwd(), search_parent_directories=True)
    except git.InvalidGitRepositoryError:
        return "Not a git repository"
    except Exception as e:
        return f"Error accessing git: {e}"
    
    try:
        from datetime import timedelta
        cutoff_date = datetime.now() - timedelta(days=days)
        
        commits = []
        for commit in repo.iter_commits(max_count=100):
            commit_date = datetime.fromtimestamp(commit.committed_date)
            if commit_date < cutoff_date:
                break
            commits.append(commit)
        
        if not commits:
            return f"No commits found in the last {days} days"
        
        summary = [f"# CHANGES IN LAST {days} DAYS\n"]
        summary.append(f"Total commits: {len(commits)}\n")
        
        authors = {}
        for commit in commits:
            author = commit.author.name
            authors[author] = authors.get(author, 0) + 1
        
        summary.append("## Contributors")
        for author, count in sorted(authors.items(), key=lambda x: x[1], reverse=True):
            summary.append(f"- {author}: {count} commits")
        
        summary.append("\n## Recent Commits")
        for commit in commits[:10]:
            date_str = datetime.fromtimestamp(commit.committed_date).strftime('%Y-%m-%d %H:%M')
            message = commit.message.strip().split('\n')[0][:100]
            summary.append(f"- **{date_str}** [{commit.hexsha[:7]}]: {message}")
        
        if len(commits) > 10:
            summary.append(f"\n... and {len(commits) - 10} more commits")
        
        return '\n'.join(summary)
    except Exception as e:
        return f"Error analyzing changes: {e}"


@mcp.tool()
def index_changed_files() -> str:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    coll = get_vector_store()
    if coll is None:
        return "Failed to initialize vector store."

    metadata = IndexMetadata()

    root_dir = Path(".")
    ignored_dirs = get_ignored_dirs()
    ignore_patterns = load_index_ignore_patterns()

    all_files = []
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for file in files:
            file_path = Path(root) / file
            if should_index_file(file_path, ignore_patterns):
                all_files.append(file_path)

    changed_files = metadata.get_changed_files(all_files)

    if not changed_files:
        return "No changed files to index."

    documents = []
    metadatas = []
    ids = []

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )

    log(f"Found {len(changed_files)} changed files...")
    file_count = 0
    chunk_count = 0

    for file_path in changed_files:
        try:
            content = safe_read_text(file_path)
            if not content.strip():
                continue

            chunks = text_splitter.split_text(content)

            for i, chunk in enumerate(chunks):
                documents.append(chunk)
                metadatas.append({"source": str(file_path), "chunk_index": i})
                ids.append(f"{file_path}_{i}")

            mtime = file_path.stat().st_mtime
            metadata.update_file(str(file_path), mtime)

            file_count += 1
            chunk_count += len(chunks)

        except (UnicodeDecodeError, IOError) as e:
            log(f"Skipping {file_path}: {e}")
        except Exception as e:
            log(f"Skipping {file_path}: {e}")

    if documents:
        log(f"Indexing {len(documents)} chunks from {file_count} files...")
        try:
            for i in range(0, len(documents), BATCH_SIZE):
                end = min(i + BATCH_SIZE, len(documents))
                collection.upsert(
                    documents=documents[i:end], metadatas=metadatas[i:end], ids=ids[i:end]
                )

            existing_files = {str(f) for f in all_files}
            metadata.remove_deleted_files(existing_files)
            metadata.save()

            return f"Incrementally indexed {file_count} changed files ({chunk_count} chunks)."
        except Exception as e:
            log(f"Error during indexing: {e}")
            return f"Error during indexing: {e}"
    else:
        metadata.save()
        return "No documents found to index."


@mcp.tool()
def search_codebase_advanced(
    query: str,
    n_results: int = 5,
    file_types: Optional[List[str]] = None,
    exclude_dirs: Optional[List[str]] = None,
    min_relevance: float = 0.0,
) -> str:
    if not query or not query.strip():
        return "Error: Query cannot be empty."

    if n_results <= 0:
        return "Error: n_results must be greater than 0."

    if n_results > 50:
        return "Error: n_results cannot exceed 50."

    if min_relevance < 0 or min_relevance > 1:
        return "Error: min_relevance must be between 0 and 1."

    coll = get_vector_store()
    if coll is None:
        return "Vector store not initialized."

    try:
        results = coll.query(query_texts=[query], n_results=n_results * 2)

        output = []
        if results["documents"]:
            for i in range(len(results["documents"][0])):
                doc = results["documents"][0][i]
                meta = results["metadatas"][0][i]
                source = meta.get("source", "unknown")
                distance = results.get("distances", [[]])[0][i] if "distances" in results else 0

                relevance = 1 - (distance / 2)

                if min_relevance > 0 and relevance < min_relevance:
                    continue

                if file_types:
                    file_ext = Path(source).suffix
                    if file_ext not in file_types:
                        continue

                if exclude_dirs:
                    skip = False
                    for exc_dir in exclude_dirs:
                        if exc_dir in source:
                            skip = True
                            break
                    if skip:
                        continue

                output.append(f"--- {source} (relevance: {relevance:.2f}) ---\n{doc}\n")

                if len(output) >= n_results:
                    break

        return "\n".join(output) if output else "No matches found."
    except Exception as e:
        log(f"Search error: {e}")
        return f"Error during search: {e}"


@mcp.tool()
def auto_update_memory_from_commits(days: int = 7, auto_summarize: bool = True) -> str:
    if days <= 0 or days > 90:
        return "Error: days must be between 1 and 90"

    try:
        repo = git.Repo(os.getcwd(), search_parent_directories=True)
    except git.InvalidGitRepositoryError:
        return "Not a git repository"
    except Exception as e:
        return f"Error accessing git: {e}"

    try:
        cutoff_date = datetime.now() - timedelta(days=days)
        commits = []

        for commit in repo.iter_commits(max_count=100):
            commit_date = datetime.fromtimestamp(commit.committed_date)
            if commit_date < cutoff_date:
                break
            commits.append(commit)

        if not commits:
            return f"No commits found in the last {days} days"

        if auto_summarize and len(commits) > 5:
            summary_lines = [f"## Auto-Summary ({days} days)"]
            summary_lines.append(f"Total commits: {len(commits)}")

            authors = {}
            for commit in commits:
                author = commit.author.name
                authors[author] = authors.get(author, 0) + 1

            summary_lines.append("\n**Contributors:**")
            for author, count in sorted(authors.items(), key=lambda x: x[1], reverse=True):
                summary_lines.append(f"- {author}: {count} commits")

            summary_lines.append("\n**Key Changes:**")
            for commit in commits[:10]:
                message = commit.message.strip().split("\n")[0][:100]
                summary_lines.append(f"- {message}")

            summary_text = "\n".join(summary_lines)
            update_memory(summary_text, section="Recent Activity")

            return f"Auto-summarized {len(commits)} commits into memory"
        else:
            ingested = ingest_git_history(limit=len(commits))
            return f"Auto-update: {ingested}"

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def analyze_code_complexity(target_path: str = ".") -> str:
    try:
        from radon.complexity import cc_visit
        from radon.metrics import mi_visit
    except ImportError:
        return "Error: radon not installed. Run: pip install radon"
    
    try:
        target = validate_path(target_path)
        if not target.exists():
            return f"Path not found: {target_path}"

        results = []
        results.append("# CODE COMPLEXITY ANALYSIS\n")

        py_files = list(target.rglob("*.py"))
        ignored_dirs = get_ignored_dirs()
        py_files = [f for f in py_files if not any(ig in str(f) for ig in ignored_dirs)]

        if not py_files:
            return "No Python files found"

        high_complexity = []
        total_complexity = 0
        file_count = 0

        for py_file in py_files[:50]:
            try:
                code = py_file.read_text(encoding="utf-8")

                complexity_results = cc_visit(code)
                if complexity_results:
                    for item in complexity_results:
                        if item.complexity > 10:
                            high_complexity.append((str(py_file), item.name, item.complexity))
                        total_complexity += item.complexity

                file_count += 1
            except Exception:
                continue

        if high_complexity:
            results.append("## High Complexity Functions (>10)")
            high_complexity.sort(key=lambda x: x[2], reverse=True)
            for file, name, complexity in high_complexity[:20]:
                results.append(f"- `{file}:{name}` - Complexity: {complexity}")

        avg_complexity = total_complexity / file_count if file_count > 0 else 0
        results.append(f"\n## Summary")
        results.append(f"- Files analyzed: {file_count}")
        results.append(f"- High complexity functions: {len(high_complexity)}")
        results.append(f"- Average complexity: {avg_complexity:.2f}")

        return "\n".join(results)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error analyzing complexity: {e}"


@mcp.tool()
def analyze_code_quality(target_path: str = ".", max_files: int = 10) -> str:
    try:
        from pylint.lint import Run
        from io import StringIO
    except ImportError:
        return "Error: pylint not installed. Run: pip install pylint"
    
    try:
        target = validate_path(target_path)
        if not target.exists():
            return f"Path not found: {target_path}"

        py_files = list(target.rglob("*.py"))
        ignored_dirs = get_ignored_dirs()
        py_files = [f for f in py_files if not any(ig in str(f) for ig in ignored_dirs)]

        if not py_files:
            return "No Python files found"

        results = []
        results.append("# CODE QUALITY ANALYSIS\n")

        files_to_check = py_files[:max_files]
        results.append(f"Analyzing {len(files_to_check)} files...\n")

        issues_summary = {"convention": 0, "refactor": 0, "warning": 0, "error": 0}

        for py_file in files_to_check:
            try:
                old_stdout = sys.stdout
                sys.stdout = StringIO()

                pylint_output = Run([str(py_file), "--output-format=text"], exit=False)

                sys.stdout = old_stdout

                if hasattr(pylint_output.linter.stats, "by_msg"):
                    for msg_type in pylint_output.linter.stats.by_msg:
                        count = pylint_output.linter.stats.by_msg[msg_type]
                        if "convention" in msg_type.lower():
                            issues_summary["convention"] += count
                        elif "refactor" in msg_type.lower():
                            issues_summary["refactor"] += count
                        elif "warning" in msg_type.lower():
                            issues_summary["warning"] += count
                        elif "error" in msg_type.lower():
                            issues_summary["error"] += count

            except Exception:
                sys.stdout = old_stdout
                continue

        results.append("## Issues Summary")
        results.append(f"- Errors: {issues_summary['error']}")
        results.append(f"- Warnings: {issues_summary['warning']}")
        results.append(f"- Refactoring suggestions: {issues_summary['refactor']}")
        results.append(f"- Convention issues: {issues_summary['convention']}")

        total_issues = sum(issues_summary.values())
        if total_issues > 0:
            results.append(f"\n**Total issues found**: {total_issues}")
            results.append("\nRun pylint directly for detailed reports.")
        else:
            results.append("\n✅ No major issues found!")

        return "\n".join(results)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error analyzing quality: {e}"


@mcp.tool()
def get_test_coverage_info() -> str:
    try:
        coverage_file = Path(".coverage")
        htmlcov_dir = Path("htmlcov")

        if not coverage_file.exists() and not htmlcov_dir.exists():
            return "No coverage data found. Run: pytest --cov=. --cov-report=html"

        results = []
        results.append("# TEST COVERAGE INFO\n")

        if htmlcov_dir.exists():
            index_file = htmlcov_dir / "index.html"
            if index_file.exists():
                content = index_file.read_text()

                if "pc_cov" in content:
                    import re

                    match = re.search(r'<span class="pc_cov">(\d+)%</span>', content)
                    if match:
                        coverage = match.group(1)
                        results.append(f"**Overall Coverage**: {coverage}%\n")

                results.append("Coverage report available at: htmlcov/index.html")

        if coverage_file.exists():
            results.append(f"\nCoverage data file found: {coverage_file}")
            results.append("Run: coverage report")

        return "\n".join(results) if results else "Coverage data exists but couldn't parse it"
    except Exception as e:
        return f"Error reading coverage: {e}"


@mcp.tool()
def save_memory_version(description: str = "") -> str:
    if not MEMORY_FILE.exists():
        return "Memory file not found"

    try:
        MEMORY_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        version_file = MEMORY_HISTORY_DIR / f"memory_{timestamp}.md"

        shutil.copy2(MEMORY_FILE, version_file)

        metadata_file = MEMORY_HISTORY_DIR / f"memory_{timestamp}.meta.json"
        metadata = {
            "timestamp": timestamp,
            "description": description,
            "created_at": datetime.now().isoformat(),
        }

        with open(metadata_file, "w") as f:
            json.dump(metadata, f, indent=2)

        return f"Memory version saved: {version_file.name}"
    except Exception as e:
        return f"Error saving version: {e}"


@mcp.tool()
def list_memory_versions() -> str:
    if not MEMORY_HISTORY_DIR.exists():
        return "No memory versions found"

    try:
        versions = []
        for meta_file in sorted(MEMORY_HISTORY_DIR.glob("*.meta.json"), reverse=True):
            try:
                with open(meta_file, "r") as f:
                    meta = json.load(f)

                timestamp = meta.get("timestamp", "unknown")
                description = meta.get("description", "")
                created = meta.get("created_at", "")

                version_info = f"- **{timestamp}**"
                if description:
                    version_info += f": {description}"
                if created:
                    version_info += f" ({created})"

                versions.append(version_info)
            except Exception:
                continue

        if not versions:
            return "No memory versions found"

        return "# MEMORY VERSIONS\n\n" + "\n".join(versions)
    except Exception as e:
        return f"Error listing versions: {e}"


@mcp.tool()
def restore_memory_version(timestamp: str) -> str:
    if not MEMORY_HISTORY_DIR.exists():
        return "No memory versions found"

    try:
        version_file = MEMORY_HISTORY_DIR / f"memory_{timestamp}.md"

        if not version_file.exists():
            return f"Version not found: {timestamp}"

        save_memory_version(description="Auto-backup before restore")

        shutil.copy2(version_file, MEMORY_FILE)

        return f"Memory restored from version: {timestamp}"
    except Exception as e:
        return f"Error restoring version: {e}"


if __name__ == "__main__":
    mcp.run()

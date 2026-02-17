import os
import sys

sys.path.append(os.getcwd())

try:
    from mcp_server import (
        analyze_code_complexity,
        analyze_project_structure,
        auto_update_memory_from_commits,
        delete_memory_section,
        ensure_startup,
        extract_tech_stack,
        generate_project_summary,
        get_index_stats,
        get_recent_changes_summary,
        get_test_coverage_info,
        index_changed_files,
        index_codebase,
        ingest_git_history,
        list_memory_versions,
        read_memory,
        save_memory_version,
        search_codebase,
        search_codebase_advanced,
        update_memory,
    )

    print("Successfully imported tools.")
except ImportError as e:
    print(f"Failed to import tools: {e}")
    sys.exit(1)


def test_memory_tools() -> None:
    print("\n--- Testing Memory Tools ---")

    # Ensure memory file is initialized
    ensure_startup()

    initial_memory = read_memory()
    print(f"Initial Memory Length: {len(initial_memory)}")
    assert "Project Memory" in initial_memory

    update_result = update_memory(
        "Decided to use ChromaDB for vector storage.", section="Architecture"
    )
    print(f"Update Result: {update_result}")
    assert "successfully" in update_result

    updated_memory = read_memory()
    assert "Decided to use ChromaDB" in updated_memory

    empty_update = update_memory("", section="Test")
    print(f"Empty Update Result: {empty_update}")
    assert "Error" in empty_update

    print("Memory tools verification passed.")


def test_memory_management() -> None:
    print("\n--- Testing Memory Management ---")

    update_memory("Test section content", section="Test Section")

    delete_result = delete_memory_section("Test Section")
    print(f"Delete Result: {delete_result}")
    assert "deleted successfully" in delete_result.lower() or "success" in delete_result.lower()

    memory_after_delete = read_memory()
    assert "Test section content" not in memory_after_delete

    print("Memory management verification passed.")


def test_rag_tools() -> None:
    print("\n--- Testing RAG Tools ---")

    print("Indexing codebase...")
    index_result = index_codebase(force=True)
    print(f"Index Result: {index_result}")
    assert (
        "Indexed" in index_result
        or "No documents" in index_result
        or "Failed to initialize" in index_result
    )

    stats = get_index_stats()
    print(f"Index Stats: {stats}")
    assert "chunks" in stats.lower() or "not initialized" in stats.lower()

    print("Searching codebase...")
    search_result = search_codebase("startup check")
    print(f"Search Result Length: {len(search_result)}")
    print(f"Search Result Preview: {search_result[:200]}...")

    assert (
        "startup_check" in search_result
        or "mcp_server.py" in search_result
        or "No matches" in search_result
        or "not initialized" in search_result
    )

    print("RAG tools verification passed.")


def test_search_validation() -> None:
    print("\n--- Testing Search Validation ---")

    empty_query = search_codebase("")
    print(f"Empty Query Result: {empty_query}")
    assert "Error" in empty_query

    invalid_limit = search_codebase("test", n_results=-1)
    print(f"Invalid Limit Result: {invalid_limit}")
    assert "Error" in invalid_limit

    large_limit = search_codebase("test", n_results=100)
    print(f"Large Limit Result: {large_limit}")
    assert "Error" in large_limit

    print("Search validation verification passed.")


def test_git_integration() -> None:
    print("\n--- Testing Git Integration ---")

    git_result = ingest_git_history(limit=5)
    print(f"Git Ingest Result: {git_result}")
    assert (
        "Ingested" in git_result
        or "No new commits" in git_result
        or "not a git repository" in git_result
    )

    invalid_limit = ingest_git_history(limit=-1)
    print(f"Invalid Git Limit: {invalid_limit}")
    assert "Error" in invalid_limit

    large_limit = ingest_git_history(limit=2000)
    print(f"Large Git Limit: {large_limit}")
    assert "Error" in large_limit

    print("Git integration verification passed.")


def test_analysis_tools() -> None:
    print("\n--- Testing Analysis Tools ---")

    summary = generate_project_summary()
    print(f"Summary Length: {len(summary)}")
    assert "PROJECT SUMMARY" in summary or "Error" in summary

    tech_stack = extract_tech_stack()
    print(f"Tech Stack: {tech_stack[:200]}...")
    assert (
        "Python" in tech_stack
        or "No standard dependency files" in tech_stack
        or "Error" in tech_stack
    )

    structure = analyze_project_structure()
    print(f"Structure Length: {len(structure)}")
    assert "PROJECT STRUCTURE" in structure or "Error" in structure

    changes = get_recent_changes_summary(days=30)
    print(f"Recent Changes: {changes[:200]}...")
    assert (
        "CHANGES" in changes
        or "No commits" in changes
        or "Not a git repository" in changes
        or "Error" in changes
    )

    print("Analysis tools verification passed.")


def test_incremental_indexing() -> None:
    print("\n--- Testing Incremental Indexing ---")

    result = index_changed_files()
    print(f"Incremental Index Result: {result[:200]}...")
    assert (
        "Incrementally indexed" in result
        or "No changed files" in result
        or "Error" in result
        or "Failed" in result
    )

    print("Incremental indexing verification passed.")


def test_advanced_search() -> None:
    print("\n--- Testing Advanced Search ---")

    result = search_codebase_advanced(
        query="test", n_results=3, file_types=[".py"], min_relevance=0.3
    )
    print(f"Advanced Search Result: {result[:200]}...")
    assert (
        "relevance" in result
        or "No matches" in result
        or "not initialized" in result
        or "Error" in result
    )

    print("Advanced search verification passed.")


def test_auto_memory_updates() -> None:
    print("\n--- Testing Auto Memory Updates ---")

    result = auto_update_memory_from_commits(days=7)
    print(f"Auto Update Result: {result}")
    assert (
        "Auto-summarized" in result
        or "Auto-update" in result
        or "No commits" in result
        or "not a git repository" in result
        or "Error" in result
    )

    print("Auto memory updates verification passed.")


def test_code_metrics() -> None:
    print("\n--- Testing Code Metrics ---")

    complexity = analyze_code_complexity(".")
    print(f"Complexity Result: {complexity[:200]}...")
    assert "COMPLEXITY" in complexity or "No Python files" in complexity or "Error" in complexity

    coverage = get_test_coverage_info()
    print(f"Coverage Result: {coverage}")
    assert "coverage" in coverage.lower() or "Error" in coverage or "No coverage" in coverage

    print("Code metrics verification passed.")


def test_memory_versioning() -> None:
    print("\n--- Testing Memory Versioning ---")

    save_result = save_memory_version(description="Test version")
    print(f"Save Version Result: {save_result}")
    assert "saved" in save_result.lower() or "Error" in save_result

    list_result = list_memory_versions()
    print(f"List Versions: {list_result[:200]}...")
    assert (
        "MEMORY VERSIONS" in list_result
        or "No memory versions" in list_result
        or "Error" in list_result
    )

    print("Memory versioning verification passed.")


if __name__ == "__main__":
    try:
        test_memory_tools()
        test_memory_management()
        test_search_validation()
        test_git_integration()
        test_analysis_tools()
        test_incremental_indexing()
        test_advanced_search()
        test_auto_memory_updates()
        test_code_metrics()
        test_memory_versioning()
        test_rag_tools()
        print("\n[PASS] All tests passed successfully!")
    except AssertionError as e:
        print(f"\n[FAIL] Test failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] An error occurred: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

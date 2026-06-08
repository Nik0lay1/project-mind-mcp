import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

from memory_manager import MemoryManager, _score_block, _split_into_blocks, _tokenize

SAMPLE = """# Project Memory

## Architecture
The system uses a tiered query router with L0 L1 L2 layers.

## Recent Decisions

### Update (Database)
We migrated from sqlite to postgres for scaling.

### Update (Auth)
Switched authentication to JWT tokens.
"""


def _mm(tmp_path: Path) -> MemoryManager:
    f = tmp_path / "memory.md"
    f.write_text(SAMPLE, encoding="utf-8")
    return MemoryManager(memory_file=f)


class TestTokenize:
    def test_lowercases_and_drops_short_tokens(self):
        assert _tokenize("Postgres a JWT_token") == {"postgres", "jwt_token"}

    def test_empty(self):
        assert _tokenize("") == set()


class TestSplitIntoBlocks:
    def test_splits_sections_and_subsections(self):
        blocks = _split_into_blocks(SAMPLE)
        headings = [h for h, _ in blocks]
        assert "Architecture" in headings
        assert "Recent Decisions / Update (Database)" in headings
        assert "Recent Decisions / Update (Auth)" in headings

    def test_body_is_captured(self):
        blocks = dict(_split_into_blocks(SAMPLE))
        assert "postgres" in blocks["Recent Decisions / Update (Database)"]


class TestScoreBlock:
    def test_heading_match_outweighs_body_match(self):
        tokens = {"database"}
        heading_hit = _score_block("Update (Database)", "unrelated body", tokens)
        body_hit = _score_block("Update (Auth)", "uses a database here", tokens)
        assert heading_hit > body_hit

    def test_no_overlap_is_zero(self):
        assert _score_block("Auth", "jwt tokens", {"kubernetes"}) == 0.0


class TestSearchBlocks:
    def test_empty_query_returns_empty(self, tmp_path: Path):
        assert _mm(tmp_path).search_blocks("") == []

    def test_missing_file_returns_empty(self, tmp_path: Path):
        mm = MemoryManager(memory_file=tmp_path / "nope.md")
        assert mm.search_blocks("anything") == []

    def test_body_keyword_match_ranks_first(self, tmp_path: Path):
        out = _mm(tmp_path).search_blocks("postgres")
        assert out
        assert out[0][0] == "Recent Decisions / Update (Database)"

    def test_heading_keyword_match(self, tmp_path: Path):
        out = _mm(tmp_path).search_blocks("authentication jwt")
        assert out[0][0] == "Recent Decisions / Update (Auth)"

    def test_architecture_query(self, tmp_path: Path):
        out = _mm(tmp_path).search_blocks("tiered router")
        assert out[0][0] == "Architecture"

    def test_k_limit_is_respected(self, tmp_path: Path):
        out = _mm(tmp_path).search_blocks("update", k=1)
        assert len(out) == 1

    def test_irrelevant_query_returns_nothing(self, tmp_path: Path):
        assert _mm(tmp_path).search_blocks("kubernetes helm istio") == []

    def test_results_sorted_by_score_desc(self, tmp_path: Path):
        out = _mm(tmp_path).search_blocks("update database postgres", k=5)
        scores = [s for _, s, _ in out]
        assert scores == sorted(scores, reverse=True)

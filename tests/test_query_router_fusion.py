import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from query_router import QueryHit, _merge_hits


def _h(source: str, score: float, tier: str, snippet: str = "") -> QueryHit:
    return QueryHit(source=source, score=score, tier=tier, snippet=snippet)


class TestMergeHitsBasics:
    def test_empty_returns_empty(self):
        assert _merge_hits([], 5) == []
        assert _merge_hits([[], []], 5) == []

    def test_single_bucket_preserves_order(self):
        bucket = [_h("a", 1.0, "L0"), _h("b", 1.0, "L0"), _h("c", 1.0, "L0")]
        out = _merge_hits([bucket], 5)
        assert [h.source for h in out] == ["a", "b", "c"]

    def test_single_bucket_top_is_normalized_to_one(self):
        bucket = [_h("a", 1.0, "L0"), _h("b", 1.0, "L0")]
        out = _merge_hits([bucket], 5)
        assert out[0].score == 1.0
        assert out[1].score < 1.0

    def test_limit_is_respected(self):
        bucket = [_h(f"f{i}", 1.0, "L0") for i in range(10)]
        out = _merge_hits([bucket], 3)
        assert len(out) == 3


class TestCrossTierFusion:
    def test_corroborated_item_outranks_single_tier_items(self):
        # "b" appears in both tiers; it should beat "a" (only L0, rank 0)
        l0 = [_h("a", 1.0, "L0"), _h("b", 1.0, "L0"), _h("c", 1.0, "L0")]
        l1 = [_h("b", 9.0, "L1"), _h("x", 8.0, "L1")]
        out = _merge_hits([l0, l1], 10)
        assert out[0].source == "b"

    def test_raw_bm25_magnitude_does_not_dominate(self):
        # "x" has a huge raw BM25 score but ranks below "a" because RRF is
        # rank-based and scale-invariant: "a" is rank 0 in L0, "x" is rank 1 in L1.
        l0 = [_h("a", 1.0, "L0"), _h("b", 1.0, "L0")]
        l1 = [_h("b", 999.0, "L1"), _h("x", 998.0, "L1")]
        out = _merge_hits([l0, l1], 10)
        sources = [h.source for h in out]
        assert sources[0] == "b"
        assert sources.index("a") < sources.index("x")

    def test_scores_are_bounded_0_1(self):
        l0 = [_h("a", 1.0, "L0"), _h("b", 1.0, "L0")]
        l1 = [_h("b", 5.0, "L1"), _h("x", 4.0, "L1")]
        out = _merge_hits([l0, l1], 10)
        for h in out:
            assert 0.0 <= h.score <= 1.0

    def test_item_top_in_every_tier_scores_one(self):
        l0 = [_h("z", 1.0, "L0")]
        l2 = [_h("z", 0.42, "L2")]
        out = _merge_hits([l0, l2], 5)
        assert out[0].source == "z"
        assert out[0].score == 1.0


class TestRepresentativeSelection:
    def test_keeps_highest_raw_score_representative(self):
        l0 = [_h("p", 1.0, "L0", snippet="from-l0")]
        l2 = [_h("p", 0.3, "L2", snippet="from-l2")]
        out = _merge_hits([l0, l2], 5)
        assert out[0].tier == "L0"
        assert out[0].snippet == "from-l0"

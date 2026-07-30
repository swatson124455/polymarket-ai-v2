"""Phase-3 unified allocation key (operator-named 2026-07-30; BUILT NOT ENABLED).

_alloc_priority: flag OFF -> the pool dict verbatim; flag ON -> cap_score from the shadowed
capital-aware key with the sweeper's pcap merged (age-cutoff); any fault -> fail-open to the
pool dict. Series rotation follows rank order when the flag is ON.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402
import kalshi_capital_rank as kcr                   # noqa: E402
import kalshi_market_scores as kms                  # noqa: E402

NOW = datetime.datetime(2026, 7, 30, 12, 0, tzinfo=datetime.timezone.utc)
ROWS = [{"ticker": "BIG-1", "usd_day": 1000.0},
        {"ticker": "SMALL-1", "usd_day": 10.0}]
USD = {"BIG-1": 1000.0, "SMALL-1": 10.0}


class TestAllocPriority:
    def test_flag_off_returns_pool_dict_verbatim(self):
        assert q.ALLOC_KEY == 0                       # ships off
        assert q._alloc_priority(ROWS, NOW, USD) is USD

    def test_flag_on_returns_cap_scores(self, monkeypatch):
        monkeypatch.setattr(q, "ALLOC_KEY", 1)
        monkeypatch.setattr(q, "SCORES", {})
        monkeypatch.setattr(q, "_load_fill_costs", lambda: {})
        monkeypatch.setattr(q, "_load_prospective", lambda: {})
        prio = q._alloc_priority(ROWS, NOW, USD)
        assert set(prio) == {"BIG-1", "SMALL-1"}
        assert prio is not USD and all(isinstance(v, float) for v in prio.values())

    def test_measured_burner_ranks_below_clean_small_market(self, monkeypatch):
        monkeypatch.setattr(q, "ALLOC_KEY", 1)
        monkeypatch.setattr(q, "ALLOC_RISK_LAMBDA", 2.0)
        scores = {}
        kms.update(scores, "BIG-1", 5.0, 0.5, now=NOW.timestamp())      # measured, meh
        kms.update(scores, "SMALL-1", 5.0, 0.5, now=NOW.timestamp())    # measured, same base
        monkeypatch.setattr(q, "SCORES", scores)
        monkeypatch.setattr(q, "_load_fill_costs",
                            lambda: {"BIG-1": {"cost_usd_day": 8.0}})   # proven burner
        monkeypatch.setattr(q, "_load_prospective", lambda: {})
        prio = q._alloc_priority(ROWS, NOW, USD)
        assert prio["SMALL-1"] > prio["BIG-1"]        # lost money lowers future allocation

    def test_sweeper_pcap_merged_with_age_cutoff(self, monkeypatch):
        monkeypatch.setattr(q, "ALLOC_KEY", 1)
        scores = {}
        kms.update_prospective(scores, "FRESH-1", 7.0, 0.4, now=NOW.timestamp() - 60)
        kms.update_prospective(scores, "OLD-1", 7.0, 0.4,
                               now=NOW.timestamp() - q.ALLOC_PCAP_MAX_AGE_S - 1)
        monkeypatch.setattr(q, "SCORES", scores)
        monkeypatch.setattr(q, "_load_fill_costs", lambda: {})
        monkeypatch.setattr(q, "_load_prospective", lambda: {})
        seen = {}

        def spy(rows, sc, costs, mc, ih, now, **kw):
            seen.update(kw.get("prospective") or {})
            return []

        monkeypatch.setattr(kcr, "shadow_rank", spy)
        q._alloc_priority(ROWS, NOW, USD)
        assert "FRESH-1" in seen and seen["FRESH-1"]["capture"] == 7.0
        assert "OLD-1" not in seen                    # past the age cutoff

    def test_fail_open_to_pool_dict(self, monkeypatch):
        monkeypatch.setattr(q, "ALLOC_KEY", 1)
        monkeypatch.setattr(q, "SCORES", {})
        monkeypatch.setattr(q, "_load_fill_costs", lambda: {})
        monkeypatch.setattr(q, "_load_prospective", lambda: {})

        def boom(*a, **k):
            raise RuntimeError("scoring fault")

        monkeypatch.setattr(kcr, "shadow_rank", boom)
        before = q._SILENT["alloc_key_fail"]
        assert q._alloc_priority(ROWS, NOW, USD) is USD
        assert q._SILENT["alloc_key_fail"] == before + 1


class TestSeriesRotationRankOrder:
    def _progs(self):
        end = (NOW + datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        start = (NOW - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return [{"market_ticker": t, "period_reward": pr, "target_size_fp": 100,
                 "discount_factor_bps": 5000, "incentive_type": "liquidity",
                 "start_date": start, "end_date": end}
                for t, pr in (("BIGPOOL-1", 10000000), ("SMALLPOOL-1", 100000))]

    def test_rotation_follows_rank_when_flag_on(self, monkeypatch):
        monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 0)      # skip market-clock REST prefilter
        monkeypatch.setattr(q, "SCORE_RANK", 1)
        scores = {}
        # SMALLPOOL measured GREAT, BIGPOOL measured awful -> rank puts SMALLPOOL first
        kms.update(scores, "SMALLPOOL-1", 500.0, 0.5, now=NOW.timestamp())
        kms.update(scores, "BIGPOOL-1", 0.01, 0.5, now=NOW.timestamp())
        monkeypatch.setattr(q, "SCORES", scores)
        monkeypatch.setattr(q, "SCORE_EXPLORE", 0)
        monkeypatch.setattr(q, "ALLOC_KEY", 1)
        picked = q.select_footprint(self._progs(), NOW)
        assert picked[0]["ticker"] == "SMALLPOOL-1"         # rank order wins rotation
        monkeypatch.setattr(q, "ALLOC_KEY", 0)
        picked = q.select_footprint(self._progs(), NOW)
        assert picked[0]["ticker"] == "BIGPOOL-1"           # legacy: pool order

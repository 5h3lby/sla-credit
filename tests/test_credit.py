"""Uptime and credit: exact tier boundaries, caps, rounding, unknown-minute range, refusal on ambiguity."""

from decimal import Decimal
from fractions import Fraction

import pytest
from helpers import OCT, UB, buckets, incident, terms

from sla_credit.engine import ContractNotCalculable, compute_result, credit_mnt, lookup_credit_pct, month_window

W = month_window(OCT, UB)
FEE = 12_500_000
T = terms()


def run(bs, incs=(), t=T):
    return compute_result(month=OCT, fee_mnt=FEE, terms=t, buckets=bs, incidents=list(incs))[0]


@pytest.mark.parametrize(
    "uptime,pct",
    [
        (Fraction(100), 0),
        (Fraction("99.9"), 0),  # meets target exactly -> no credit
        (Fraction("99.8999999"), 10),
        (Fraction("99.0"), 10),  # tier bands are [min, max)
        (Fraction("98.9999999"), 25),
        (Fraction("95.0"), 25),
        (Fraction("94.9999999"), 50),
        (Fraction(0), 50),
    ],
)
def test_tier_boundaries_are_exact(uptime, pct):
    assert lookup_credit_pct(T.credit_tiers, T.uptime_target, uptime) == pct


def test_44_vs_45_down_minutes_straddle_99_9():
    # 44640 min in Oct; 99.9% allows 44.64 down minutes
    assert run(buckets(W, down=[("2026-10-10T10:00", "2026-10-10T10:44")])).credit_mnt == 0
    r = run(buckets(W, down=[("2026-10-10T10:00", "2026-10-10T10:45")]))
    assert r.uptime_pct == Decimal("99.8991") and r.credit_pct == 10 and r.credit_mnt == 1_250_000


def test_display_truncates_so_it_never_rounds_up_into_compliance():
    r = run(buckets(W, down=[("2026-10-10T10:00", "2026-10-10T10:45")]))
    assert float(r.uptime_pct) < 99.9 and r.uptime_pct == Decimal("99.8991")  # exact 99.89919...


def test_cap_limits_credit_below_the_tier():
    t = terms(credit_cap_pct=20, citations=T.citations)
    r = run(buckets(W, down=[("2026-10-01T00:00", "2026-10-03T00:00")]), t=t)  # 2 days -> 93.5%
    assert r.credit_pct == 20 and r.credit_mnt == 2_500_000


@pytest.mark.parametrize(
    "fee,pct,expected",
    [(1_234_567, "10", 123_457), (5, "10", 1), (15, "2.5", 0), (3, "50", 2), (12_500_000, "33.3", 4_162_500)],
)
def test_round_half_up_to_integer_mnt(fee, pct, expected):
    assert credit_mnt(fee, Decimal(pct)) == expected


def test_unknown_minutes_produce_a_best_worst_range():
    r = run(buckets(W, down=[("2026-10-10T10:00", "2026-10-10T10:30")],
                    unknown=[("2026-10-11T03:00", "2026-10-11T04:00")]))
    assert (r.down_min, r.unknown_min) == (30, 60)
    assert r.credit_mnt == 0  # unknown is neither up nor down
    assert r.credit_range.best_case_mnt == 0
    assert r.credit_range.worst_case_mnt == 1_250_000  # if the gap was really an outage


def test_missing_buckets_are_unknown_not_up():
    r = run(buckets(W)[:-60])  # last hour of the month has no data at all
    assert r.unknown_min == 60


def test_whole_month_excluded_means_100_percent_not_division_by_zero():
    fm = incident("2026-09-25T00:00", "2026-11-05T00:00", "force_majeure")
    r = run(buckets(W, down=[("2026-10-01T00:00", "2026-10-31T00:00")]), [fm])
    assert r.measured_min == 0 and r.uptime_pct == 100 and r.credit_mnt == 0


def test_down_minutes_inside_an_exclusion_do_not_count():
    cc = incident("2026-10-10T10:00", "2026-10-10T12:00", "customer_caused")
    r = run(buckets(W, down=[("2026-10-10T10:00", "2026-10-10T11:00")]), [cc])
    assert (r.excluded_min, r.down_min, r.credit_mnt) == (120, 0, 0)


def test_engine_refuses_ambiguous_contract():
    with pytest.raises(ContractNotCalculable, match="min_notice_hours"):
        run(buckets(W, service="storage-api"), t=terms("contract_tricky_annex.json"))

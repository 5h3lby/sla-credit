"""A realistic month end-to-end through the engine, reconciliation flags, and output invariants."""

from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest
from helpers import OCT, UB, buckets, load, terms
from pydantic import ValidationError

from sla_credit.engine import compute_result, month_window, reconcile
from sla_credit.models import (
    CustomerSlaResult,
    FlagKind,
    Incident,
    ResultLine,
    ResultLineKind,
    RunSummary,
)

W = month_window(OCT, UB)
T = terms()
INCIDENTS = [Incident.model_validate(x) for x in load("incidents_recorded.json")]
BUCKETS = buckets(W, down=[
    ("2026-10-04T02:00", "2026-10-04T04:00"),  # OPS-101 maintenance, too little notice
    ("2026-10-13T22:00", "2026-10-13T23:30"),  # OPS-102 provider fault
    ("2026-10-25T10:00", "2026-10-25T10:20"),  # nobody filed a ticket
])


@pytest.fixture(scope="module")
def month():
    result, ex = compute_result(month=OCT, fee_mnt=12_500_000, terms=T, buckets=BUCKETS, incidents=INCIDENTS)
    flags = reconcile(terms=T, month=OCT, buckets=BUCKETS, incidents=INCIDENTS, exclusions=ex)
    return result, ex, flags


def test_month_totals(month):
    r, ex, _ = month
    assert (r.total_min, r.excluded_min, r.measured_min, r.down_min) == (44640, 60, 44580, 230)
    assert str(r.uptime_pct) == "99.4840"
    assert (r.credit_pct, r.credit_mnt) == (10, 1_250_000)
    assert ex.review_queue == ["OPS-103"]


def test_every_line_traces_to_minutes_tickets_and_clauses(month):
    r, _, _ = month
    by_ticket = {(line.kind, line.incident_id): line for line in r.lines}
    pm = by_ticket[(ResultLineKind.DOWNTIME, "OPS-101")]
    assert pm.minutes == 120 and "not excluded" in pm.reason and pm.contract_clause == "Хавсралт 2, 3.2"
    ex = by_ticket[(ResultLineKind.EXCLUDED, "OPS-104")]
    assert ex.minutes == 60 and ex.end_utc == W.end and ex.contract_clause
    assert [line.start_utc for line in r.lines] == sorted(line.start_utc for line in r.lines)


def test_all_four_reconciliation_flags_fire(month):
    _, _, flags = month
    assert Counter(f.kind for f in flags) == {
        FlagKind.SHORT_NOTICE_MAINTENANCE: 1,  # OPS-101
        FlagKind.UNREPORTED_OUTAGE: 1,  # Oct 25, no ticket: "the system caught it"
        FlagKind.TICKET_WITHOUT_OUTAGE: 2,  # OPS-103 (low conf) and OPS-104 (logs up)
    }
    unreported = next(f for f in flags if f.kind is FlagKind.UNREPORTED_OUTAGE)
    assert unreported.start_utc == datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc)
    assert "20 min" in unreported.detail


def test_run_summary_and_slack_text(month):
    r, ex, flags = month
    run = RunSummary(run_id="run-1", month=OCT, results=[r], flags=flags, review_queue=ex.review_queue)
    assert run.total_credit_mnt == 1_250_000 and run.customers_affected == 1
    assert run.slack_text() == (
        "SLA credits 2026-10: 1,250,000 MNT owed · 1/1 customers affected · "
        "4 reconciliation flags · 1 tickets awaiting review"
    )
    assert RunSummary.model_validate_json(run.model_dump_json()) == run


def test_result_whose_books_do_not_balance_cannot_be_built(month):
    r, _, _ = month
    data = r.model_dump()
    for patch, msg in [
        ({"down_min": 231}, "downtime lines sum to 230"),
        ({"excluded_min": 61}, "measured_min != total_min - excluded_min"),
        ({"credit_mnt": 99}, "within credit_range"),
        ({"credit_mnt": 20_000_000}, "exceeds monthly fee"),
    ]:
        with pytest.raises(ValidationError, match=msg):
            CustomerSlaResult.model_validate(data | patch)


def test_result_line_invariants():
    t = datetime(2026, 10, 1, tzinfo=timezone.utc)
    ok = {"start_utc": t, "end_utc": t + timedelta(minutes=5), "minutes": 5, "kind": "downtime", "reason": "x"}
    ResultLine.model_validate(ok)
    with pytest.raises(ValidationError, match="range spans 5"):
        ResultLine.model_validate(ok | {"minutes": 4})
    with pytest.raises(ValidationError, match="must cite an incident"):
        ResultLine.model_validate(ok | {"kind": "excluded", "incident_id": "OPS-1"})
    with pytest.raises(ValidationError, match="whole UTC minute"):
        ResultLine.model_validate(ok | {"start_utc": t + timedelta(seconds=30), "end_utc": t + timedelta(minutes=5, seconds=30)})


def test_run_summary_rejects_duplicate_rows_and_wrong_month(month):
    r, _, _ = month
    with pytest.raises(ValidationError, match="duplicate customer"):
        RunSummary(run_id="r", month=OCT, results=[r, r])
    with pytest.raises(ValidationError, match="run month"):
        RunSummary(run_id="r", month="2026-11", results=[r])
    with pytest.raises(ValidationError, match="pattern"):
        RunSummary(run_id="r", month="2026-13", results=[])

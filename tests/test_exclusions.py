"""Exclusion decisions: overlaps, notice periods, monthly cap, month crossing, low confidence."""

from helpers import OCT, UB, incident, load, terms

from sla_credit.engine import decide_exclusions, month_window
from sla_credit.models import FlagKind, Incident

W = month_window(OCT, UB)


def decide(*incs, t=None):
    return decide_exclusions(list(incs), t or terms(), W)


def test_overlapping_incidents_are_merged_never_double_counted():
    a = incident("2026-10-05T10:00", "2026-10-05T11:00", "customer_caused", ticket_id="OPS-1")
    b = incident("2026-10-05T10:30", "2026-10-05T12:00", "customer_caused", ticket_id="OPS-2")
    ex = decide(b, a)
    assert len(ex.excluded) == 120  # not 150
    owners = {v[0] for v in ex.excluded.values()}
    assert owners == {"OPS-1", "OPS-2"}  # first by start time owns the overlap


def test_short_notice_maintenance_is_not_excluded_and_flagged():
    ops101 = Incident.model_validate(load("incidents_recorded.json")[0])  # notice ~16.8h, contract needs 72h
    ex = decide(ops101)
    assert not ex.excluded
    assert len(ex.rejected) == 120
    assert ex.flags[0].kind is FlagKind.SHORT_NOTICE_MAINTENANCE
    assert "16.8h < 72h" in ex.flags[0].detail


def test_maintenance_without_any_notice_is_short_notice():
    ex = decide(incident("2026-10-06T02:00", "2026-10-06T03:00", "planned_maintenance", notified_at=None))
    assert not ex.excluded and ex.flags[0].kind is FlagKind.SHORT_NOTICE_MAINTENANCE


def test_exactly_72h_notice_is_enough():
    ex = decide(incident("2026-10-06T02:00", "2026-10-06T03:00", "planned_maintenance",
                         notified_at="2026-10-03T02:00:00+08:00"))
    assert len(ex.excluded) == 60 and not ex.flags


def test_monthly_maintenance_cap_excess_counts_as_downtime():
    note = "2026-10-01T00:00:00+08:00"
    incs = [incident(f"2026-10-{d}T01:00", f"2026-10-{d}T03:00", "planned_maintenance",
                     ticket_id=f"OPS-{d}", notified_at=note) for d in (10, 11, 12)]
    ex = decide(*incs)
    assert len(ex.excluded) == 240  # 4h cap
    assert len(ex.rejected) == 120
    assert [f.kind for f in ex.flags] == [FlagKind.MAINTENANCE_CAP_EXCEEDED]
    assert ex.flags[0].ticket_id == "OPS-12"


def test_maintenance_overlapping_force_majeure_does_not_burn_cap():
    note = "2026-10-01T00:00:00+08:00"
    fm = incident("2026-10-10T00:00", "2026-10-10T04:00", "force_majeure", ticket_id="OPS-1")
    pm = incident("2026-10-10T00:00", "2026-10-10T04:00", "planned_maintenance", ticket_id="OPS-2", notified_at=note)
    pm2 = incident("2026-10-20T00:00", "2026-10-20T04:00", "planned_maintenance", ticket_id="OPS-3", notified_at=note)
    ex = decide(fm, pm, pm2)
    assert len(ex.excluded) == 480 and not ex.flags


def test_incident_crossing_month_boundary_is_split_in_contract_timezone():
    ops104 = Incident.model_validate(load("incidents_recorded.json")[3])  # Oct 31 23:00 -> Nov 1 03:00 UB
    assert len(decide(ops104).excluded) == 60
    nov = decide_exclusions([ops104], terms(), month_window("2026-11", UB))
    assert len(nov.excluded) == 180


def test_low_confidence_goes_to_review_queue_even_with_odd_service_casing():
    ops103 = Incident.model_validate(load("incidents_recorded.json")[2])  # 0.55, "Lending-API"
    ex = decide(ops103)
    assert ex.review_queue == ["OPS-103"] and not ex.excluded


def test_only_fully_covered_minutes_are_excluded():
    ex = decide(incident("2026-10-05T10:00:30", "2026-10-05T10:05:00", "customer_caused"))
    assert len(ex.excluded) == 4


def test_provider_fault_and_uncontracted_categories_are_never_excluded():
    no_fm = terms(exclusions=[{"type": "customer_caused"}])
    ex = decide(incident("2026-10-05T10:00", "2026-10-05T11:00", "provider_fault"),
                incident("2026-10-06T10:00", "2026-10-06T11:00", "force_majeure"),
                incident("2026-10-07T10:00", "2026-10-07T11:00", "unknown"), t=no_fm)
    assert not ex.excluded


def test_other_services_are_ignored():
    ex = decide(incident("2026-10-05T10:00", "2026-10-05T11:00", "customer_caused",
                         affected_services=["storage-api"]))
    assert not ex.excluded

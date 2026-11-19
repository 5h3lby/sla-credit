"""Tickets CSV (BOM, multiline, Cyrillic/transliterated/English, emoji) and the Incident LLM contract."""

from datetime import datetime, timedelta, timezone

import pytest
from helpers import FIX, UB, load
from pydantic import ValidationError

from sla_credit.ingest import load_tickets
from sla_credit.models import Incident

BASE = load("incidents_recorded.json")[1]  # OPS-102, "өчигдөр оройн 10 цагаас"


@pytest.fixture(scope="module")
def loaded():
    return load_tickets(FIX / "tickets_messy.csv", default_tz=UB)


def test_good_rows_load_despite_bom_and_spaced_headers(loaded):
    tickets, _ = loaded
    assert [t.ticket_id for t in tickets] == ["OPS-101", "OPS-102", "OPS-103", "OPS-104"]


def test_bad_rows_are_rejected_with_reasons_not_dropped(loaded):
    _, rejected = loaded
    assert len(rejected) == 4  # missing id, free-text created_at, empty text, lowercase id
    assert any("OPS-106" in r for r in rejected)
    assert any("ops-108" in r for r in rejected)


def test_text_survives_multiline_quotes_scripts_and_emoji(loaded):
    t = {t.ticket_id: t for t in loaded[0]}
    assert "\n" in t["OPS-101"].text and "02:00-04:00" in t["OPS-101"].text
    assert '"Нарлаг Зээл"' in t["OPS-102"].text
    assert "tsagaas" in t["OPS-103"].text
    assert "🔧" in t["OPS-104"].text


def test_naive_created_at_is_ulaanbaatar_time(loaded):
    t = {t.ticket_id: t for t in loaded[0]}
    assert t["OPS-101"].created_at == datetime(2026, 10, 3, 1, 12, tzinfo=timezone.utc)
    assert t["OPS-103"].created_at == datetime(2026, 10, 20, 1, 5, tzinfo=timezone.utc)


def test_recorded_llm_outputs_validate_against_their_tickets(loaded):
    created = {t.ticket_id: t.created_at for t in loaded[0]}
    for raw in load("incidents_recorded.json"):
        inc = Incident.model_validate(raw, context={"ticket_created_at": created[raw["ticket_id"]]})
        assert inc.end > inc.start


def test_yesterday_misresolved_by_a_year_is_caught_by_plausibility_check():
    bad = BASE | {"start": "2025-10-13T22:00:00+08:00", "end": "2025-10-13T23:30:00+08:00"}
    created = datetime.fromisoformat("2026-10-14T23:40:00+08:00")
    Incident.model_validate(bad)  # structurally fine...
    with pytest.raises(ValidationError, match="more than 7 days"):
        Incident.model_validate(bad, context={"ticket_created_at": created})  # ...but implausible


@pytest.mark.parametrize(
    "patch,msg",
    [
        ({"end": BASE["start"]}, "must be after start"),
        ({"start": "2026-10-13T22:00:00"}, "timezone"),  # naive: the LLM dropped the offset
        ({"confidence": 1.2}, "less than or equal to 1"),
        ({"category": "outage"}, "planned_maintenance"),
        ({"affected_services": []}, "at least 1"),
        ({"evidence": "x" * 301}, "at most 300"),
        ({"duration_minutes": 90}, "Extra inputs"),  # the LLM must not do arithmetic
        ({"ticket_id": "102"}, "pattern"),
    ],
)
def test_incident_contract_rejects_bad_llm_output(patch, msg):
    with pytest.raises(ValidationError, match=msg):
        Incident.model_validate(BASE | patch)


def test_offsets_are_preserved_and_comparable():
    a = Incident.model_validate(BASE)
    b = Incident.model_validate(BASE | {"start": "2026-10-13T14:00:00Z", "end": "2026-10-13T15:30:00Z"})
    assert a.start == b.start and a.end - a.start == timedelta(minutes=90)

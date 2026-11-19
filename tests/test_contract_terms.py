"""ContractTerms LLM contract: citations, ambiguities, tier semantics, exact decimals."""

from decimal import Decimal

import pytest
from helpers import load, terms
from pydantic import ValidationError

from sla_credit.models import ContractTerms

MN = load("contract_mn_errorrate.json")


def bad(**override):
    with pytest.raises(ValidationError) as e:
        ContractTerms.model_validate(MN | override)
    return str(e.value)


def test_mongolian_contract_is_valid_and_calculable():
    t = terms()
    assert t.is_calculable
    assert t.uptime_target == Decimal("99.9")  # exact, not 99.90000000000000568
    assert t.citations["credit_tiers"] == "5.2 дугаар зүйл"


def test_tricky_annex_contract_is_normalized_but_blocked_on_vague_notice():
    t = terms("contract_tricky_annex.json")
    assert [x.min for x in t.credit_tiers] == [0, 90, 99]  # annex order fixed
    assert t.uptime_target == Decimal("99.5")  # strings from the LLM coerced exactly
    assert not t.is_calculable
    assert t.ambiguities[0].field == "exclusions.planned_maintenance.min_notice_hours"


def test_null_without_ambiguity_is_a_guess_and_rejected():
    msg = bad(uptime_target=None)
    assert "uptime_target is null but not listed in ambiguities" in msg


def test_vague_notice_must_be_listed_as_ambiguity():
    ex = [{"type": "planned_maintenance", "min_notice_hours": None, "max_hours_per_month": 4}]
    assert "min_notice_hours is null" in bad(exclusions=ex)


def test_every_value_needs_a_citation():
    cites = {k: v for k, v in MN["citations"].items() if k != "credit_tiers"}
    assert "credit_tiers has a value but no citation" in bad(citations=cites)


@pytest.mark.parametrize(
    "tiers,msg",
    [
        ([{"min": 99.0, "max": 99.9, "credit_pct": 10}, {"min": 0, "max": 99.5, "credit_pct": 25}], "overlap"),
        ([{"min": 99.0, "max": 99.9, "credit_pct": 10}, {"min": 0, "max": 98.0, "credit_pct": 25}], "gap"),
        ([{"min": 95.0, "max": 99.9, "credit_pct": 10}], "must start at 0"),
        ([{"min": 0, "max": 99.5, "credit_pct": 10}], "target is 99.9"),
        ([{"min": 99.0, "max": 99.9, "credit_pct": 25}, {"min": 0, "max": 99.0, "credit_pct": 10}], "must not decrease"),
        ([{"min": 99.9, "max": 99.0, "credit_pct": 10}], "must be < max"),
        ([{"min": 0, "max": 99.9, "credit_pct": 150}], "less than or equal to 100"),
    ],
)
def test_tier_semantics(tiers, msg):
    assert msg in bad(credit_tiers=tiers)


@pytest.mark.parametrize(
    "override,msg",
    [
        ({"timezone": "Ulaanbaatar"}, "unknown IANA timezone"),
        ({"timezone": "UTC+8"}, "unknown IANA timezone"),
        ({"measurement_window": "rolling_30_days"}, "calendar_month"),
        ({"downtime_definition": {"type": "latency", "p99_ms": 500}}, "does not match any of the expected tags"),
        ({"uptime_target": 100.1}, "less than or equal to 100"),
        ({"credit_amount_mnt": 1250000}, "Extra inputs"),  # LLM tried to compute money
    ],
)
def test_structural_rejections(override, msg):
    assert msg in bad(**override)

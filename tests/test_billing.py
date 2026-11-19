"""Billing CSV: MNT in every format a human types, and loud failures for money that is wrong."""

import pytest
from helpers import FIX
from pydantic import ValidationError

from sla_credit.ingest import load_billing
from sla_credit.models import BillingRow


def test_messy_file_parses_to_integer_mnt():
    rows = {r.customer: r.monthly_fee_mnt for r in load_billing(FIX / "billing_messy.csv")}
    assert rows == {
        "Нарлаг Зээл ББСБ": 12_500_000,  # "12,500,000₮"
        "Steppe Pay LLC": 4_800_000,  # padded name, "4 800 000 MNT"
        "Altan Cloud": 3_500_000,  # "3500000.00"
        "Хөх Тэнгэр ХХК": 2_000_000,  # "2 000 000 төг"
        "Tsenher Data": 750_000,  # "750_000"; blank row above it skipped
    }
    assert all(isinstance(v, int) for v in rows.values())


@pytest.mark.parametrize("fee", ["₮1 500 000", "1 500 000", "1'500'000", "1500000 төгрөг", 1_500_000])
def test_accepted_spellings(fee):
    assert BillingRow(customer="x", monthly_fee_mnt=fee).monthly_fee_mnt == 1_500_000


@pytest.mark.parametrize(
    "fee,msg",
    [
        ("1,250,000.50", "whole tögrög"),
        (1250000.0, "not float"),
        ("-5", "greater than 0"),
        ("0", "greater than 0"),
        ("", "cannot parse"),
        ("TBD", "cannot parse"),
        ("1.5M", "cannot parse"),
        (True, "boolean"),
    ],
)
def test_rejected_fees(fee, msg):
    with pytest.raises(ValidationError, match=msg):
        BillingRow(customer="x", monthly_fee_mnt=fee)


def test_duplicate_customer_differing_only_in_case_is_fatal(tmp_path):
    f = tmp_path / "b.csv"
    f.write_text("customer,monthly_fee_mnt\nAltan Cloud,1000\nALTAN  CLOUD,2000\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_billing(f)


def test_bad_row_fails_the_whole_file_with_row_number(tmp_path):
    f = tmp_path / "b.csv"
    f.write_text("customer,monthly_fee_mnt\nA,1000\nB,12.5\n", encoding="utf-8")
    with pytest.raises(ValueError, match="billing row 3"):
        load_billing(f)


def test_blank_customer_rejected():
    with pytest.raises(ValidationError):
        BillingRow(customer="   ", monthly_fee_mnt=1000)

"""Shared test helpers (not a test file)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sla_credit.engine import Window
from sla_credit.models import ContractTerms, Incident, MinuteBucket, MinuteStatus

FIX = Path(__file__).parent / "fixtures"
UB = "Asia/Ulaanbaatar"
OCT = "2026-10"


def load(name: str) -> Any:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def terms(name: str = "contract_mn_errorrate.json", **override: Any) -> ContractTerms:
    return ContractTerms.model_validate({**load(name), **override})


def ub(s: str) -> datetime:
    """'2026-10-13T22:00' -> aware datetime in Ulaanbaatar (+08:00, no DST)."""
    return datetime.fromisoformat(s + "+08:00")


def incident(start: str, end: str, category: str = "provider_fault", **kw: Any) -> Incident:
    data = {
        "ticket_id": "OPS-1",
        "start": ub(start),
        "end": ub(end),
        "category": category,
        "affected_services": ["lending-api"],
        "confidence": 0.9,
        "evidence": "test",
    } | kw
    return Incident.model_validate(data)


def buckets(
    w: Window,
    *,
    service: str = "lending-api",
    down: list[tuple[str, str]] = (),  # type: ignore[assignment]
    unknown: list[tuple[str, str]] = (),  # type: ignore[assignment]
) -> list[MinuteBucket]:
    """A whole window of 'up' minutes, with UB-local [start, end) ranges marked down/unknown."""
    status: dict[int, MinuteStatus] = {}
    for ranges, s in ((down, MinuteStatus.DOWN), (unknown, MinuteStatus.UNKNOWN)):
        for a, b in ranges:
            for i in range(w.idx_floor(ub(a)), w.idx_floor(ub(b))):
                status[i] = s
    return [
        MinuteBucket.model_construct(
            service=service, minute_utc=w.at(i), total_requests=10,
            err_5xx=10 if status.get(i) is MinuteStatus.DOWN else 0,
            status=status.get(i, MinuteStatus.UP),
        )
        for i in range(w.minutes)
    ]

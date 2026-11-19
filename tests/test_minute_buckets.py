"""Minute buckets: error-rate threshold, missing logs != no traffic, probes, month windows."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from sla_credit.engine import Window, build_buckets, month_window
from sla_credit.models import ErrorRateDowntime, LogRecord, MinuteBucket, MinuteStatus, ProbeDowntime, ProbeResult

T0 = datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc)
W = Window(T0, T0 + timedelta(hours=3))
ER5 = ErrorRateDowntime(type="error_rate", threshold_pct=5, window_minutes=1)


def recs(minute: int, ok: int, err: int, service: str = "api", code: int = 502) -> list[LogRecord]:
    ts = T0 + timedelta(minutes=minute, seconds=7)
    return [LogRecord(ts_utc=ts, service=service, status=200, path="/")] * ok + [
        LogRecord(ts_utc=ts, service=service, status=code, path="/")
    ] * err


def status(bs: list[MinuteBucket], minute: int) -> MinuteStatus:
    return bs[minute].status


def test_threshold_is_inclusive_and_4xx_do_not_count():
    data = recs(0, 95, 5) + recs(1, 96, 4) + recs(2, 0, 1) + recs(3, 50, 50, code=404)
    bs = build_buckets(data, service="api", window=W, definition=ER5)
    assert [status(bs, i) for i in range(4)] == ["down", "up", "down", "up"]
    assert bs[0].total_requests == 100 and bs[0].err_5xx == 5


def test_silent_minute_during_busy_hour_is_unknown_not_up():
    # hour 0: 5 req/min except minute 30 (nginx itself was down -> nothing logged)
    data = [r for m in range(60) if m != 30 for r in recs(m, 5, 0)]
    bs = build_buckets(data, service="api", window=W, definition=ER5)
    assert status(bs, 30) is MinuteStatus.UNKNOWN
    assert status(bs, 29) is MinuteStatus.UP


def test_silent_minute_at_quiet_hour_is_up():
    data = recs(120, 1, 0)  # hour 2 has one request total; hour 1 has none
    bs = build_buckets(data, service="api", window=W, definition=ER5)
    assert status(bs, 90) is MinuteStatus.UP and status(bs, 150) is MinuteStatus.UP


def test_probes_resolve_unknown_minutes():
    data = [r for m in range(60) if m not in (10, 11, 12) for r in recs(m, 5, 0)]
    probes = [
        ProbeResult(service="api", ts_utc=T0 + timedelta(minutes=10, seconds=5), ok=True),
        ProbeResult(service="api", ts_utc=T0 + timedelta(minutes=11, seconds=5), ok=True),
        ProbeResult(service="api", ts_utc=T0 + timedelta(minutes=11, seconds=35), ok=False),  # any failure wins
    ]
    bs = build_buckets(data, service="api", window=W, definition=ER5, probes=probes)
    assert [status(bs, m) for m in (10, 11, 12)] == ["up", "down", "unknown"]


def test_probe_contract_ignores_log_errors_and_needs_probe_data():
    data = recs(0, 0, 100)
    probes = [ProbeResult(service="api", ts_utc=T0, ok=True)]
    bs = build_buckets(data, service="api", window=W, definition=ProbeDowntime(type="probe"), probes=probes)
    assert status(bs, 0) is MinuteStatus.UP
    assert status(bs, 1) is MinuteStatus.UNKNOWN


def test_other_services_and_out_of_window_records_are_ignored():
    data = recs(0, 0, 10, service="other") + [
        LogRecord(ts_utc=T0 - timedelta(seconds=1), service="api", status=500, path="/"),
        LogRecord(ts_utc=W.end, service="api", status=500, path="/"),  # end is exclusive
    ]
    bs = build_buckets(data, service="api", window=W, definition=ER5)
    assert len(bs) == 180 and all(b.total_requests == 0 for b in bs)


def test_multi_minute_windows_fail_loudly():
    with pytest.raises(NotImplementedError):
        build_buckets([], service="api", window=W,
                      definition=ErrorRateDowntime(type="error_rate", threshold_pct=5, window_minutes=5))


@pytest.mark.parametrize(
    "month,tz,start,minutes",
    [
        ("2026-10", "Asia/Ulaanbaatar", "2026-09-30T16:00:00+00:00", 31 * 1440),
        ("2026-12", "Asia/Ulaanbaatar", "2026-11-30T16:00:00+00:00", 31 * 1440),  # year wrap
        ("2028-02", "UTC", "2028-02-01T00:00:00+00:00", 29 * 1440),  # leap year
        ("2026-03", "Europe/Berlin", "2026-02-28T23:00:00+00:00", 31 * 1440 - 60),  # DST: 23h day
    ],
)
def test_month_window_in_contract_timezone(month, tz, start, minutes):
    w = month_window(month, tz)
    assert w.start == datetime.fromisoformat(start) and w.minutes == minutes


def test_bucket_model_rejects_impossible_counts_and_unaligned_minutes():
    with pytest.raises(ValidationError, match="err_5xx cannot exceed"):
        MinuteBucket(service="a", minute_utc=T0, total_requests=1, err_5xx=2, status="down")
    with pytest.raises(ValidationError, match="aligned"):
        MinuteBucket(service="a", minute_utc=T0 + timedelta(seconds=5), total_requests=0, err_5xx=0, status="up")

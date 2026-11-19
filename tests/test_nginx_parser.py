"""Messy Nginx logs: two formats, mixed UTC/UB/naive timestamps, broken lines, gzip, bad bytes."""

import gzip
from datetime import datetime, timezone

import pytest
from helpers import FIX, UB
from pydantic import ValidationError

from sla_credit.ingest import parse_line, read_log_file
from sla_credit.models import LogRecord

LOG = FIX / "access_mixed.log"


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_mixed_file_counts_every_bad_line_and_skips_blank_ones():
    records, report = read_log_file(LOG, service="lending-api", default_tz=UB)
    assert report.parsed == len(records) == 9
    assert report.unparseable == 7  # truncated, 31/Sep, status 999, "Okt", time=yesterday, no status, html
    assert "<html><body>502 Bad Gateway</body></html>" in report.bad_samples
    assert not report.truncated


def test_every_timestamp_is_normalized_to_utc():
    records, _ = read_log_file(LOG, service="lending-api", default_tz=UB)
    assert all(r.ts_utc.utcoffset().total_seconds() == 0 for r in records)
    ts = [r.ts_utc for r in records]
    assert ts[0] == utc("2026-10-17T06:03:11")  # +0800 combined
    assert ts[1] == utc("2026-10-17T06:03:12")  # +0000 combined
    assert ts[3] == utc("2026-10-17T06:03:50")  # naive key=value -> assumed UB
    assert ts[8] == utc("2026-10-17T06:04:34")  # combined with offset missing -> assumed UB


def test_naive_time_uses_the_default_tz_not_utc():
    line = "time=2026-10-17T14:00:00 host=x status=200"
    assert parse_line(line, service="x", default_tz=UB).ts_utc == utc("2026-10-17T06:00:00")
    assert parse_line(line, service="x", default_tz="UTC").ts_utc == utc("2026-10-17T14:00:00")


def test_garbage_request_line_still_counts_as_traffic():
    rec = parse_line('10.0.0.9 - - [17/Oct/2026:14:04:32 +0800] "-" 400 0 "-" "-"', service="s", default_tz=UB)
    assert rec is not None and rec.path == "-" and rec.status == 400


def test_host_field_overrides_file_service_and_ipv6_is_fine():
    records, _ = read_log_file(LOG, service="from-filename", default_tz=UB)
    assert {r.service for r in records} == {"from-filename", "lending-api"}
    assert any(r.path == "/api/rates" for r in records)  # IPv6 client, HTTP/2


def test_gzip_rotated_file_gives_identical_result(tmp_path):
    gz = tmp_path / "access.log.1.gz"
    gz.write_bytes(gzip.compress(LOG.read_bytes()))
    plain, _ = read_log_file(LOG, service="lending-api", default_tz=UB)
    packed, report = read_log_file(gz, service="lending-api", default_tz=UB)
    assert packed == plain and report.unparseable == 7


def test_truncated_gzip_returns_partial_data_and_says_so(tmp_path):
    data = gzip.compress((LOG.read_text() * 50).encode())
    gz = tmp_path / "access.log.2.gz"
    gz.write_bytes(data[: len(data) // 2])
    records, report = read_log_file(gz, service="lending-api", default_tz=UB)
    assert report.truncated
    assert 0 < len(records) < 9 * 50


def test_crlf_and_invalid_utf8_do_not_crash(tmp_path):
    f = tmp_path / "win.log"
    f.write_bytes(
        b'10.0.0.1 - - [17/Oct/2026:14:00:00 +0800] "GET / HTTP/1.1" 200 1 "-" "-"\r\n'
        b"\xff\xfe\x00garbage\r\n"
    )
    records, report = read_log_file(f, service="s", default_tz=UB)
    assert len(records) == 1 and report.unparseable == 1


@pytest.mark.parametrize("ts", [datetime(2026, 10, 17, 6), datetime.fromisoformat("2026-10-17T14:00:00+08:00")])
def test_log_record_rejects_naive_or_non_utc(ts):
    with pytest.raises(ValidationError):
        LogRecord(ts_utc=ts, service="s", status=200, path="/")

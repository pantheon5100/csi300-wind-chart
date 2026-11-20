#!/usr/bin/env python3
"""Refresh the comparison from public daily closes; use only Python's standard library.

The Wind series is a third-party public mirror, not an authenticated Wind feed.
Its 2004-12-31 baseline is approximately 989.30, inferred from rounded cumulative
returns. A failed download or validation never replaces the last good snapshot.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


STOCK_CODE = "H00300"
WIND_CODE = "885003.WI"
BASE_DATE = "2004-12-31"
FIRST_COMMON_DATE = "2005-01-04"
STOCK_BASE = 1000.0
WIND_BASE = 989.3
WIND_ANCHORS = {"2005-01-04": 984.85, "2026-11-13": 4990.73}
STOCK_ENDPOINT = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
WIND_URL = "https://ashare.laoqianriritan.com/data/fund_index.json"
COLUMNS = ["date", "stock_level", "wind_level", "stock_return_pct", "wind_return_pct"]
MIN_HISTORY_ROWS = 5000
MAX_UNMATCHED_DATES = 5
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "site" / "data.json"


class DataValidationError(ValueError):
    """Source data cannot safely replace the published snapshot."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DataValidationError(message)


def parse_date(value: Any, *, compact: bool = False) -> str:
    require(isinstance(value, str), f"Date must be a string, got {type(value).__name__}")
    try:
        parsed = datetime.strptime(value, "%Y%m%d" if compact else "%Y-%m-%d").date()
    except ValueError as exc:
        raise DataValidationError(f"Invalid date: {value!r}") from exc
    expected = parsed.strftime("%Y%m%d") if compact else parsed.isoformat()
    require(value == expected, f"Noncanonical date: {value!r}")
    return parsed.isoformat()


def number(value: Any, label: str, *, positive: bool = True) -> float:
    require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{label}: expected a number")
    result = float(value)
    require(math.isfinite(result) and (not positive or result > 0), f"{label}: invalid value {value!r}")
    return result


def stock_url(today: date) -> str:
    return STOCK_ENDPOINT + "?" + urlencode({"indexCode": STOCK_CODE, "startDate": "20041231", "endDate": today.strftime("%Y%m%d")})


def decode_json(raw: bytes, label: str) -> Any:
    def reject_constant(value: str) -> None:
        raise DataValidationError(f"{label}: nonstandard JSON value {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            require(key not in result, f"{label}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8-sig"), parse_constant=reject_constant, object_pairs_hook=reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DataValidationError(f"{label}: invalid JSON") from exc


def read_json(path: Path) -> Any:
    return decode_json(path.read_bytes(), str(path))


def fetch_json(url: str) -> Any:
    """Retry transient read failures only; no credentials or private APIs."""
    request = Request(url, headers={"User-Agent": "CSI300-Wind-Comparison/1.0", "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read(MAX_DOWNLOAD_BYTES + 1)
            require(len(raw) <= MAX_DOWNLOAD_BYTES, "Source response exceeds the size limit")
            return decode_json(raw, url)
        except HTTPError as exc:
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == 2:
                raise
        except (URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
        time.sleep(2 ** (attempt + 1))
    raise RuntimeError("Unreachable download state")


def validate_history(points: dict[str, float], label: str, today: date) -> None:
    days = list(points)
    require(len(days) >= MIN_HISTORY_ROWS, f"{label}: truncated history ({len(days)} observations)")
    require(days == sorted(days), f"{label}: dates are not strictly increasing")
    require(days[0] <= FIRST_COMMON_DATE, f"{label}: missing the beginning of the history")
    require(days[-1] <= today.isoformat(), f"{label}: future-dated observations")
    require(all(date.fromisoformat(day).weekday() < 5 for day in days), f"{label}: unexpected weekend observation")
    counts = Counter(day[:4] for day in days)
    last_year = int(days[-1][:4])
    for year in range(2005, last_year):
        require(counts[str(year)] >= 200, f"{label}: incomplete year {year}")
    for previous, current in zip(days, days[1:]):
        gap = (date.fromisoformat(current) - date.fromisoformat(previous)).days
        require(gap <= 21, f"{label}: unexplained {gap}-day gap ending {current}")


def parse_stock(payload: Any, today: date) -> dict[str, float]:
    require(isinstance(payload, dict) and str(payload.get("code")) == "200", "H00300: unsuccessful source response")
    records = payload.get("data")
    require(isinstance(records, list), "H00300: missing daily data")
    points: dict[str, float] = {}
    for record in records:
        require(isinstance(record, dict) and record.get("indexCode") == STOCK_CODE, "H00300: wrong index identity")
        day = parse_date(record.get("tradeDate"), compact=True)
        require(day not in points, f"H00300: duplicate date {day}")
        points[day] = number(record.get("close"), f"H00300 {day}")
    validate_history(points, STOCK_CODE, today)
    require(points.get(BASE_DATE) == STOCK_BASE, "H00300: baseline must equal 1000.00 on 2004-12-31")
    return points


def parse_wind(payload: Any, today: date) -> dict[str, float]:
    require(isinstance(payload, dict) and isinstance(payload.get("series"), dict), "Wind: missing series object")
    series = payload["series"].get(WIND_CODE)
    require(isinstance(series, dict), "Wind: exact series 885003.WI is absent; substitutes are not accepted")
    name = series.get("name", "")
    require(isinstance(name, str) and "偏债" in name and "混合" in name and "中证" not in name, "Wind: unexpected index name")
    days, levels = series.get("dates"), series.get("values")
    require(isinstance(days, list) and isinstance(levels, list) and len(days) == len(levels), "Wind: date/value lengths differ")
    points: dict[str, float] = {}
    for raw_day, value in zip(days, levels):
        day = parse_date(raw_day)
        require(day not in points, f"Wind: duplicate date {day}")
        points[day] = number(value, f"Wind {day}")
    validate_history(points, WIND_CODE, today)
    for day, expected in WIND_ANCHORS.items():
        require(day in points and abs(points[day] - expected) <= 0.010000001, f"Wind: verified historical anchor changed or disappeared ({day})")
    return points


def validate_snapshot(snapshot: Any) -> None:
    """Validate previous snapshots before trusting their regression checks."""
    require(isinstance(snapshot, dict) and snapshot.get("schema_version") == 1, "Snapshot: unsupported schema")
    require(snapshot.get("columns") == COLUMNS, "Snapshot: unexpected columns")
    expected_base = {STOCK_CODE: STOCK_BASE, WIND_CODE: WIND_BASE, "wind_approximate": True}
    require(snapshot.get("baseline") == expected_base, "Snapshot: baseline identity changed")
    require(snapshot.get("start_date") == BASE_DATE, "Snapshot: start date changed")
    rows = snapshot.get("data")
    require(isinstance(rows, list) and len(rows) >= MIN_HISTORY_ROWS, "Snapshot: truncated history")
    previous_day = ""
    for row in rows:
        require(isinstance(row, list) and len(row) == 5, "Snapshot: malformed data row")
        day = parse_date(row[0])
        require(day > previous_day, "Snapshot: duplicate or unordered dates")
        previous_day = day
        for column, baseline in [(1, STOCK_BASE), (2, WIND_BASE)]:
            level = number(row[column], COLUMNS[column])
            return_pct = number(row[column + 2], COLUMNS[column + 2], positive=False)
            require(abs(return_pct - (level / baseline - 1) * 100) <= 0.000001, f"Snapshot: return calculation mismatch on {day}")
    require(rows[0] == [BASE_DATE, STOCK_BASE, WIND_BASE, 0.0, 0.0], "Snapshot: first row is not the fixed baseline")
    require(snapshot.get("as_of") == rows[-1][0], "Snapshot: as_of does not match the last observation")
    sources = snapshot.get("sources")
    require(isinstance(sources, dict), "Snapshot: missing sources")
    for code in (STOCK_CODE, WIND_CODE):
        source = sources.get(code)
        require(isinstance(source, dict), f"Snapshot: missing source for {code}")
        require(parse_date(source.get("as_of")) >= snapshot["as_of"], f"Snapshot: {code} source date is inconsistent")


def check_previous(current: dict[str, Any], previous: Any) -> None:
    validate_snapshot(previous)
    require(current["as_of"] >= previous["as_of"], "Update would regress the shared latest date")
    for code in (STOCK_CODE, WIND_CODE):
        require(current["sources"][code]["as_of"] >= previous["sources"][code]["as_of"], f"Update would regress {code}'s source date")
    current_rows = {row[0]: row for row in current["data"]}
    for old in previous["data"]:
        require(old[0] in current_rows, f"Update would remove a previously published date: {old[0]}")
        new = current_rows[old[0]]
        for column in (1, 2):
            require(abs(old[column] - new[column]) <= 0.010000001, f"Historical levels changed on {old[0]}; review source corrections before publishing")


def build_dataset(stock_payload: Any, wind_payload: Any, *, previous: Any = None, generated_at: datetime | None = None) -> dict[str, Any]:
    now = generated_at or datetime.now(timezone.utc)
    require(now.tzinfo is not None, "generated_at must have a timezone")
    now = now.astimezone(timezone.utc)
    stock = parse_stock(stock_payload, now.date())
    wind = parse_wind(wind_payload, now.date())
    common = sorted(day for day in stock.keys() & wind.keys() if day > BASE_DATE)
    require(len(common) >= MIN_HISTORY_ROWS and common[0] == FIRST_COMMON_DATE, "Insufficient common history")
    as_of = common[-1]
    common_set = set(common)
    for points, label in [(stock, STOCK_CODE), (wind, WIND_CODE)]:
        unmatched = [day for day in points if FIRST_COMMON_DATE <= day <= as_of and day not in common_set]
        require(len(unmatched) <= MAX_UNMATCHED_DATES, f"{label}: too many dates cannot be aligned ({len(unmatched)})")
    rows = [[BASE_DATE, STOCK_BASE, WIND_BASE, 0.0, 0.0]]
    rows.extend([day, stock[day], wind[day], round((stock[day] / STOCK_BASE - 1) * 100, 6), round((wind[day] / WIND_BASE - 1) * 100, 6)] for day in common)
    dataset = {
        "schema_version": 1,
        "generated_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "start_date": BASE_DATE,
        "as_of": as_of,
        "baseline": {STOCK_CODE: STOCK_BASE, WIND_CODE: WIND_BASE, "wind_approximate": True},
        "sources": {
            STOCK_CODE: {"name": "中证指数公司官网 · 沪深300全收益指数", "url": stock_url(now.date()), "as_of": max(stock)},
            WIND_CODE: {
                "name": "老钱日日谈公开看板 · 万得偏债混合型基金指数（第三方数据镜像）",
                "url": WIND_URL,
                "as_of": max(wind),
                "provider_note": str(wind_payload.get("source", "")),
                "note": "使用885003.WI原始点位；非Wind官方直连。固定历史锚用于识别意外换源，不代表每日独立交叉核验。",
            },
        },
        "columns": list(COLUMNS),
        "data": rows,
        "notes": [
            "固定基准为2004-12-31。Wind基准989.30由公开累计收益的舍入值反推，是近似值；基准行不是镜像中的原始观测。",
            "仅保留两条序列共同有数据的日期；不填补、不插值、不用其他基金指数替代。",
            "H00300保留中证官网原始历史序列；2005年末全收益历史存在已知口径不一致，未自行修正。",
        ],
    }
    validate_snapshot(dataset)
    if previous is not None:
        check_previous(dataset, previous)
    return dataset


def atomic_write_json(path: Path, dataset: dict[str, Any]) -> None:
    """Flush a temporary file on the same filesystem, then replace atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(dataset, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def update_from_payloads(output: Path, stock_payload: Any, wind_payload: Any, *, previous_path: Path | None = None, generated_at: datetime | None = None) -> dict[str, Any]:
    previous_file = previous_path or (output if output.exists() else None)
    previous = read_json(previous_file) if previous_file is not None else None
    dataset = build_dataset(stock_payload, wind_payload, previous=previous, generated_at=generated_at)
    atomic_write_json(output, dataset)
    return dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--stock-file", type=Path, help="Read a saved official H00300 JSON response")
    parser.add_argument("--wind-file", type=Path, help="Read a saved public fund_index.json (requires --stock-file)")
    parser.add_argument("--previous", type=Path, help="Previously published data.json; reject regressions and historical changes")
    args = parser.parse_args(argv)
    if bool(args.stock_file) != bool(args.wind_file):
        parser.error("--stock-file and --wind-file must be provided together")
    try:
        now = datetime.now(timezone.utc)
        stock_payload = read_json(args.stock_file) if args.stock_file else fetch_json(stock_url(now.date()))
        wind_payload = read_json(args.wind_file) if args.wind_file else fetch_json(WIND_URL)
        dataset = update_from_payloads(args.output, stock_payload, wind_payload, previous_path=args.previous, generated_at=now)
    except (DataValidationError, OSError, URLError, TimeoutError) as exc:
        print(f"Update failed; existing snapshot preserved: {exc}", file=sys.stderr)
        return 1
    print(f"Saved {len(dataset['data'])} aligned observations through {dataset['as_of']} to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

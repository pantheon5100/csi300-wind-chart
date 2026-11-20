#!/usr/bin/env python3
"""Refresh the comparison from public daily closes; use only Python's standard library.

The Wind series is a third-party public mirror, not an authenticated Wind feed.
Its 2004-12-31 baseline is approximately 989.30, inferred from rounded cumulative
returns. A failed download or validation never replaces the last good snapshot.
"""

from __future__ import annotations

import argparse
import copy
from collections import Counter
from datetime import date, datetime, timezone
import json
from itertools import product
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

# Also support importlib-based tests without installing this small project.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup_sources import fetch_csi_backup
from wind_backup import fetch_wind_backup


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
OVERLAP_WINDOW = 60
MIN_OVERLAP = 20
PROVIDERS = {
    STOCK_CODE: [
        {"provider_id": "csindex", "name": "中证指数公司官网", "url": STOCK_ENDPOINT, "role": "primary"},
        {"provider_id": "charlespai_tushare", "name": "CharlesPai 公开研究看板（标注来源 Tushare Pro）", "url": "https://research.charlespai.com/cn/", "role": "fallback"},
    ],
    WIND_CODE: [
        {"provider_id": "laoqian", "name": "老钱日日谈公开数据镜像", "url": WIND_URL, "role": "primary"},
        {"provider_id": "funddb", "name": "韭圈儿 / FundDB（公开指数收盘点位）", "url": "https://ai2.jiucaishuo.com/api/index-valuation/history?code=885003.WI&metric=close&years2=10", "role": "fallback"},
    ],
}


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


def fetch_bytes(url: str, *, payload: Any = None) -> bytes:
    """Retry transient read failures only; no credentials or private APIs."""
    headers = {"User-Agent": "CSI300-Wind-Comparison/1.1", "Accept": "application/json,text/html"}
    body = None
    if payload is not None:
        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers)
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read(MAX_DOWNLOAD_BYTES + 1)
            require(len(raw) <= MAX_DOWNLOAD_BYTES, "Source response exceeds the size limit")
            return raw
        except HTTPError as exc:
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == 2:
                raise
        except (URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
        time.sleep(2 ** (attempt + 1))
    raise RuntimeError("Unreachable download state")


def fetch_json(url: str, *, payload: Any = None) -> Any:
    return decode_json(fetch_bytes(url, payload=payload), url)


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
        history = source.get("history", [])
        require(isinstance(history, list), f"Snapshot: invalid source history for {code}")
        last_end = ""
        for segment in history:
            require(isinstance(segment, dict), "Snapshot: invalid provenance segment")
            first, last = parse_date(segment.get("from")), parse_date(segment.get("to"))
            require(first <= last <= snapshot["as_of"] and first > last_end, "Snapshot: overlapping or out-of-range provenance")
            require(segment.get("comparison_tolerance") in (0.01, 0.1), "Snapshot: invalid source precision")
            require(segment.get("provider_id") in {provider["provider_id"] for provider in PROVIDERS[code]}, "Snapshot: unknown historical provider")
            last_end = last
        if history:
            position = 0
            for row in rows:
                if code == WIND_CODE and row[0] == BASE_DATE:
                    continue
                while position < len(history) and row[0] > history[position]["to"]:
                    position += 1
                require(position < len(history) and history[position]["from"] <= row[0] <= history[position]["to"], "Snapshot: missing historical source coverage")


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
    return assemble_dataset(stock, wind, now=now, previous=previous, wind_provider_note=str(wind_payload.get("source", "")))


def assemble_dataset(stock: dict[str, float], wind: dict[str, float], *, now: datetime, previous: Any = None, wind_provider_note: str = "", source_metadata: Any = None) -> dict[str, Any]:
    validate_history(stock, STOCK_CODE, now.date())
    validate_history(wind, WIND_CODE, now.date())
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
                "provider_note": wind_provider_note,
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
    if source_metadata is not None:
        dataset["sources"] = source_metadata
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


def previous_points(previous: dict[str, Any], code: str) -> dict[str, float]:
    column = 1 if code == STOCK_CODE else 2
    return {row[0]: row[column] for row in previous["data"] if code == STOCK_CODE or row[0] != BASE_DATE}


def historical_tolerance(previous: dict[str, Any], code: str, day: str) -> float:
    """Keep the precision of any formerly appended backup observations explicit."""
    tolerance = 0.01
    for segment in previous["sources"][code].get("history", []):
        if segment["from"] <= day <= segment["to"]:
            tolerance = max(tolerance, float(segment["comparison_tolerance"]))
    return tolerance


def evaluate_candidate(candidate: dict[str, Any], code: str, previous: Any, today: date) -> str:
    """Return available/older; reject wrong identity, incompatible levels or dates.

    Primary feeds are checked across all previously published observations.
    Backups are admitted only against a recent contiguous overlap window; their
    older history is never imported. FundDB has known old date-alignment errors.
    """
    require(candidate.get("code") == code, f"{code}: wrong backup index identity")
    points = candidate.get("points")
    require(isinstance(points, dict) and len(points) >= MIN_OVERLAP, f"{code}: insufficient candidate history")
    days = list(points)
    require(days == sorted(days), f"{code}: candidate dates are unordered")
    for day, value in points.items():
        require(parse_date(day) == day, f"{code}: invalid candidate date")
        number(value, f"{code} {day}")
    require(days[-1] <= today.isoformat(), f"{code}: future candidate observation")
    candidate["as_of"] = days[-1]
    require(candidate.get("overlap_tolerance") in (0.01, 0.1), f"{code}: unsupported precision")
    if previous is None:
        require(candidate["mode"] == "primary", f"{code}: a backup requires a previously verified dataset")
        return "available"
    old = previous_points(previous, code)
    old_days = [day for day in old if day <= days[-1]]
    if candidate["mode"] == "fallback":
        # Require a continuous tail of expected dates, not a cherry-picked
        # intersection that skips recent missing dates. An older gap ends the
        # window; at least 20 consecutive observations must still remain.
        tail = []
        for day in reversed(old_days[-OVERLAP_WINDOW:]):
            if day not in points:
                break
            tail.append(day)
        old_days = list(reversed(tail))
    require(len(old_days) >= MIN_OVERLAP, f"{code}: fewer than {MIN_OVERLAP} overlapping observations")
    for day in old_days:
        require(day in points, f"{code}: overlap date missing ({day})")
        tolerance = max(candidate["overlap_tolerance"], historical_tolerance(previous, code, day))
        require(abs(points[day] - old[day]) <= tolerance + 1e-9, f"{code}: source disagrees with verified history on {day}")
    candidate["overlap_check"] = {
        "count": len(old_days), "from": old_days[0], "to": old_days[-1],
        "max_absolute_difference": round(max(abs(points[day] - old[day]) for day in old_days), 8),
        "tolerance": candidate["overlap_tolerance"],
    }
    # Old backup labels may be wrong, including an old weekend label. They are
    # never imported. validate_history below still rejects every NEW weekend.
    merged = {**old, **{day: value for day, value in points.items() if day > previous["as_of"]}}
    validate_history(dict(sorted(merged.items())), code, today)
    # A source that was already seen later must not silently go backward.
    minimum_date = max(previous["as_of"], previous["sources"][code]["as_of"])
    return "older" if days[-1] < minimum_date else "available"


def collect_candidates(code: str, previous: Any, now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates, checks = [], []
    for provider in PROVIDERS[code]:
        check = dict(provider)
        if code == STOCK_CODE and provider["role"] == "primary":
            check["url"] = stock_url(now.date())
        try:
            if provider["role"] == "primary":
                payload = fetch_json(check["url"])
                points = parse_stock(payload, now.date()) if code == STOCK_CODE else parse_wind(payload, now.date())
                candidate = {
                    "code": code, "points": points, "name": provider["name"], "url": check["url"],
                    "provider_id": provider["provider_id"], "precision": "原始指数收盘点位（0.01点）", "overlap_tolerance": 0.01,
                    "provider_note": "中证指数官网原始收盘点位" if code == STOCK_CODE else str(payload.get("source", "")),
                }
            else:
                candidate = fetch_csi_backup(fetch_bytes) if code == STOCK_CODE else fetch_wind_backup(fetch_json)
                # Use an explicit public provider page or callable GET data URL.
                candidate["data_url"] = candidate["url"]
                candidate["url"] = provider["url"]
                candidate["name"] = provider["name"]
                candidate["precision"] = "原始收盘点位；与已验证历史的允许差异不超过0.01点"
            candidate["mode"] = provider["role"]
            check["as_of"] = max(candidate["points"])
            check["status"] = evaluate_candidate(candidate, code, previous, now.date())
            check["overlap_check"] = candidate.get("overlap_check")
            checked = candidate.get("overlap_check", {}).get("count", 0)
            prefix = (f"最近连续 {checked} 个数据日校验通过" if provider["role"] == "fallback" else "已有历史校验通过")
            check["message"] = prefix + ("" if check["status"] == "available" else "；来源日期较旧，不会回退数据")
            if check["status"] == "available":
                candidates.append(candidate)
        except (ValueError, TypeError, KeyError, OSError, URLError, TimeoutError) as exc:
            check["status"] = "rejected" if isinstance(exc, (ValueError, TypeError, KeyError)) else "failed"
            check["message"] = str(exc)[:300]
        checks.append(check)
    # Prefer the freshest verified provider, breaking equal-date ties in favor of primary.
    candidates.sort(key=lambda item: (item["as_of"], item["mode"] == "primary"), reverse=True)
    return candidates, checks


def source_history(previous: Any, code: str, candidate: Any, as_of: str) -> list[dict[str, Any]]:
    history = []
    if previous is not None:
        old_source = previous["sources"][code]
        history = copy.deepcopy(old_source.get("history", []))
        if not history:
            history.append({
                "from": BASE_DATE if code == STOCK_CODE else FIRST_COMMON_DATE,
                "to": previous["as_of"], "provider_id": old_source.get("provider_id", PROVIDERS[code][0]["provider_id"]),
                "name": old_source["name"], "url": old_source["url"], "comparison_tolerance": 0.01,
                "precision": "原始指数收盘点位（0.01点）",
            })
    if candidate is None:
        return history
    new_days = [day for day in candidate["points"] if (previous is None or day > previous["as_of"]) and day <= as_of and day >= (BASE_DATE if code == STOCK_CODE else FIRST_COMMON_DATE)]
    if new_days:
        segment = {
            "from": min(new_days), "to": max(new_days), "provider_id": candidate["provider_id"],
            "name": candidate["name"], "url": candidate["url"],
            "comparison_tolerance": candidate["overlap_tolerance"], "precision": candidate["precision"],
        }
        if history and history[-1]["provider_id"] == segment["provider_id"] and history[-1]["comparison_tolerance"] == segment["comparison_tolerance"]:
            history[-1]["to"] = segment["to"]
        else:
            history.append(segment)
    return history


def prepare_pair(candidate_pair: tuple[Any, Any], previous: Any, all_checks: dict[str, Any], now: datetime) -> dict[str, Any]:
    merged, sources = {}, {}
    for code, candidate in zip((STOCK_CODE, WIND_CODE), candidate_pair):
        if candidate is None:
            require(previous is not None, f"{code}: a cached source requires verified history")
            points = previous_points(previous, code)
            source = copy.deepcopy(previous["sources"][code])
            source["mode"] = "cached"
        else:
            points = dict(candidate["points"])
            if previous is not None:
                points = {**previous_points(previous, code), **{day: value for day, value in points.items() if day > previous["as_of"]}}
                points = dict(sorted(points.items()))
            source = {key: value for key, value in candidate.items() if key not in {"points", "code", "overlap_tolerance"}}
        source["checks"] = all_checks[code]
        source["historical_source_note"] = "已发布历史保持不变；备用来源通过近期重叠数据校验后才追加新日期。"
        if code == WIND_CODE:
            source["note"] = "非Wind官方直连；主源及备用均读取原始指数收盘点位。"
        merged[code], sources[code] = points, source
    common = sorted(merged[STOCK_CODE].keys() & merged[WIND_CODE].keys())
    require(bool(common), "No shared dates")
    if previous is not None:
        # An append-only dataset cannot fill a missing interior date later.
        # Therefore NEW dates through the shared cutoff must match exactly.
        new_stock = {day for day in merged[STOCK_CODE] if previous["as_of"] < day <= common[-1]}
        new_wind = {day for day in merged[WIND_CODE] if previous["as_of"] < day <= common[-1]}
        require(new_stock == new_wind, "新增共同日期未对齐；暂不跳过缺失日期")
    return assemble_dataset(merged[STOCK_CODE], merged[WIND_CODE], now=now, previous=previous, source_metadata=sources)


def update_live(output: Path, *, previous_path: Path | None = None, generated_at: datetime | None = None, allow_cached: bool = False) -> dict[str, Any]:
    """Use verified alternatives and preserve established observations verbatim."""
    now = generated_at or datetime.now(timezone.utc)
    require(now.tzinfo is not None, "generated_at must have a timezone")
    now = now.astimezone(timezone.utc)
    previous_file = previous_path or (output if output.exists() else None)
    previous = read_json(previous_file) if previous_file is not None else None
    if previous is not None:
        validate_snapshot(previous)
        for code in (STOCK_CODE, WIND_CODE):
            validate_history(previous_points(previous, code), code, now.date())
    options, all_checks = {}, {}
    for code in (STOCK_CODE, WIND_CODE):
        candidates, checks = collect_candidates(code, previous, now)
        all_checks[code] = checks
        options[code] = candidates + ([None] if allow_cached and previous is not None else [])
        require(bool(options[code]), f"{code}: no current verified provider; existing snapshot preserved")
    valid_pairs, errors = [], []
    for pair in product(options[STOCK_CODE], options[WIND_CODE]):
        try:
            candidate_dataset = prepare_pair(pair, previous, all_checks, now)
            score = (candidate_dataset["as_of"], sum(item is not None for item in pair), sum(item is not None and item["mode"] == "primary" for item in pair))
            valid_pairs.append((score, pair, candidate_dataset))
        except DataValidationError as exc:
            errors.append(str(exc))
    require(bool(valid_pairs), "No aligned provider combination: " + "; ".join(dict.fromkeys(errors)))
    # Score the actual common cutoff, not two independent source cutoff dates.
    # A bad fresher backup must not block an otherwise valid primary pair.
    _, pair, dataset = max(valid_pairs, key=lambda item: item[0])
    selected = dict(zip((STOCK_CODE, WIND_CODE), pair))
    warnings = []
    for code, candidate in selected.items():
        dataset["sources"][code]["history"] = source_history(previous, code, candidate, dataset["as_of"])
        if candidate is None:
            warnings.append(f"{code} 本次未取得可用的新数据，沿用已验证历史。")
        elif candidate["mode"] == "fallback":
            warnings.append(f"{code} 本次使用经重叠校验的备用来源；既有历史保持不变。")
    cached = any(candidate is None for candidate in pair)
    changed = previous is None or dataset["as_of"] > previous["as_of"]
    outcome = "cached" if cached else "fresh" if changed else "unchanged"
    if cached and errors:
        warnings.append("本次两条序列未通过共同日期校验：" + errors[0])
    if cached and previous is not None:
        dataset["generated_at"] = previous["generated_at"]
    names = []
    for code, label in [(STOCK_CODE, "沪深300"), (WIND_CODE, "万得")]:
        candidate = selected[code]
        names.append(label + "：" + ("缓存" if candidate is None else "备用" if candidate["mode"] == "fallback" else "主源"))
    dataset["update_status"] = {
        "checked_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"), "outcome": outcome,
        "message": ("部分来源暂不可用或未通过校验，保留上次已验证行情。" if cached else "来源检查完成。") + "本次 " + "；".join(names) + "。", "warnings": warnings,
    }
    validate_snapshot(dataset)
    atomic_write_json(output, dataset)
    return dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--stock-file", type=Path, help="Read a saved official H00300 JSON response")
    parser.add_argument("--wind-file", type=Path, help="Read a saved public fund_index.json (requires --stock-file)")
    parser.add_argument("--previous", type=Path, help="Previously published data.json; reject regressions and historical changes")
    parser.add_argument("--allow-cached", action="store_true", help="Publish the last verified data with a visible stale-source status if all live providers fail")
    args = parser.parse_args(argv)
    if bool(args.stock_file) != bool(args.wind_file):
        parser.error("--stock-file and --wind-file must be provided together")
    try:
        now = datetime.now(timezone.utc)
        if args.stock_file:
            dataset = update_from_payloads(args.output, read_json(args.stock_file), read_json(args.wind_file), previous_path=args.previous, generated_at=now)
        else:
            dataset = update_live(args.output, previous_path=args.previous, generated_at=now, allow_cached=args.allow_cached)
    except (DataValidationError, OSError, URLError, TimeoutError) as exc:
        print(f"Update failed; existing snapshot preserved: {exc}", file=sys.stderr)
        return 1
    print(f"Saved {len(dataset['data'])} aligned observations through {dataset['as_of']} to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

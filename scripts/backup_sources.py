"""Read exact CSI 300 total-return closes from a public Tushare-backed mirror.

This adapter parses JSON embedded in the page. It never executes JavaScript and
does not call Tushare or use credentials. The caller owns transport, recency,
recent-overlap checks and append-only merging with the last published snapshot.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from html.parser import HTMLParser
import json
import math
import re
from typing import Any


CSI_BACKUP_URL = "https://research.charlespai.com/cn/"
MAX_HTML_BYTES = 8 * 1024 * 1024
MAX_JSON_CHARS = 4 * 1024 * 1024
DATA_DECLARATION = re.compile(r"(?m)^\s*const\s+DATA\s*=\s*")


class CsiBackupError(ValueError):
    """The backup page cannot be interpreted as the intended daily series."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CsiBackupError(message)


class _InlineScripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.scripts: list[str] = []
        self.current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "script":
            self.current = [] if not any(key.lower() == "src" for key, _ in attrs) else None

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self.current is not None:
            self.scripts.append("".join(self.current))
            self.current = None


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"CSI backup: duplicate JSON key {key!r}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise CsiBackupError(f"CSI backup: nonstandard JSON constant {value}")


def _embedded_data(raw: bytes) -> dict[str, Any]:
    _require(isinstance(raw, bytes), "CSI backup: expected response bytes")
    _require(len(raw) <= MAX_HTML_BYTES, "CSI backup: HTML response is too large")
    try:
        html = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise CsiBackupError("CSI backup: response is not UTF-8") from exc
    parser = _InlineScripts()
    parser.feed(html)
    parser.close()
    candidates = [(script, match.end()) for script in parser.scripts for match in DATA_DECLARATION.finditer(script)]
    _require(len(candidates) == 1, "CSI backup: missing or ambiguous const DATA declaration")
    script, start = candidates[0]
    tail = script[start:]
    _require(tail.startswith("{"), "CSI backup: DATA must be a literal JSON object")
    decoder = json.JSONDecoder(object_pairs_hook=_json_object, parse_constant=_invalid_constant)
    try:
        data, end = decoder.raw_decode(tail[: MAX_JSON_CHARS + 1])
    except (json.JSONDecodeError, RecursionError) as exc:
        raise CsiBackupError("CSI backup: invalid or oversized embedded JSON") from exc
    _require(end <= MAX_JSON_CHARS, "CSI backup: embedded JSON is too large")
    remainder = tail[end:].lstrip()
    _require(not remainder or remainder.startswith(";"), "CSI backup: DATA is not a standalone JSON assignment")
    _require(isinstance(data, dict), "CSI backup: DATA must be an object")
    return data


def parse_csi_backup(raw: bytes) -> dict[str, Any]:
    """Extract and validate DATA.tr.h00300; return unmodified daily close levels."""
    data = _embedded_data(raw)
    total_return = data.get("tr")
    _require(isinstance(total_return, dict), "CSI backup: missing total-return series group")
    series = total_return.get("h00300")
    _require(isinstance(series, dict), "CSI backup: exact h00300 series is absent")
    _require(series.get("name") in {"沪深300全收益", "沪深300全收益指数"}, "CSI backup: wrong index name or return type")
    days, levels = series.get("dates"), series.get("close")
    _require(isinstance(days, list) and isinstance(levels, list) and len(days) == len(levels) and len(days) >= 2, "CSI backup: empty or misaligned daily arrays")
    points: dict[str, float] = {}
    previous = ""
    for day, level in zip(days, levels):
        _require(isinstance(day, str), "CSI backup: date must be a string")
        try:
            canonical = date.fromisoformat(day).isoformat()
        except ValueError as exc:
            raise CsiBackupError(f"CSI backup: invalid date {day!r}") from exc
        _require(day == canonical and day > previous, "CSI backup: noncanonical, duplicate or unordered dates")
        _require(isinstance(level, (int, float)) and not isinstance(level, bool), "CSI backup: close must be numeric")
        try:
            value = float(level)
        except OverflowError as exc:
            raise CsiBackupError("CSI backup: close is outside the numeric range") from exc
        _require(math.isfinite(value) and value > 0, "CSI backup: close must be finite and positive")
        points[day] = value
        previous = day
    _require(series.get("as_of") == days[-1], "CSI backup: as_of differs from the final observation")
    if "since" in series:
        _require(series["since"] == days[0], "CSI backup: since differs from the first observation")
    if "2004-12-31" in points:
        _require(points["2004-12-31"] == 1000.0, "CSI backup: original H00300 baseline changed")
    return {
        "code": "H00300",
        "name": "沪深300全收益指数 · Tushare公开看板镜像",
        "url": CSI_BACKUP_URL,
        "points": points,
        "as_of": days[-1],
        "precision": "raw_daily_close_2dp",
        "provider_note": "research.charlespai.com公开页面标注数据来自Tushare Pro H00300.CSI；此适配器读取公开镜像，未直接连接Tushare。",
        "provider_id": "charlespai_tushare",
        "overlap_tolerance": 0.01,
    }


def fetch_csi_backup(fetch_bytes: Callable[[str], bytes]) -> dict[str, Any]:
    """Transport is supplied by the updater so retries and timeouts stay shared."""
    return parse_csi_backup(fetch_bytes(CSI_BACKUP_URL))

"""Read the exact Wind 885003.WI close series from FundDB's public site API.

These GET routes are used by FundDB's publicly served application, and were
verified without login, cookies, tokens or a Referer. Source ``close`` values
are retained directly: no return reconstruction, rescaling or normalization.

Historical FundDB labels have contained date shifts (notably May/June 2026).
This parser never shifts dates or patches history. The caller MUST check its
most recent continuous overlap (up to 60 dates, at least 20, no missing dates
within that tail, maximum absolute difference <= 0.01 point), append only later observations and reject
regressions. Source identity alone does not establish correct date alignment.
"""

from datetime import date, datetime, timedelta, timezone
import math
import re


CODE = "885003.WI"
INDEX_NAME = "万得偏债混合型基金指数"
DETAIL_URL = "https://ai2.jiucaishuo.com/api/index-valuation/detail?code=885003.WI"
HISTORY_URL = "https://ai2.jiucaishuo.com/api/index-valuation/history?code=885003.WI&metric=close&years2=10"
LEVEL_TOLERANCE = 0.01


class WindBackupError(ValueError):
    """The response cannot safely be interpreted as the requested index."""


def _require(condition, message):
    if not condition:
        raise WindBackupError("FundDB: " + message)


def _day(value):
    _require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), "noncanonical date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise WindBackupError("FundDB: invalid calendar date") from exc
    _require(parsed.isoformat() == value, "noncanonical date")
    return parsed


def _number(value):
    _require(not isinstance(value, bool) and isinstance(value, (int, float)), "invalid numeric close")
    _require(math.isfinite(value) and value > 0, "non-finite or nonpositive close")
    return float(value)


def parse_wind_backup(history_response, detail_response, *, today=None):
    """Validate raw closes and independent name/last-close metadata.

    Ten years of this feed are enough for an incremental fallback, not a new
    inception history. The caller retains its original 2004-12-31 baseline and
    all already published historical observations.
    """
    _require(isinstance(history_response, dict) and isinstance(detail_response, dict), "responses are not objects")
    _require(history_response.get("code") == CODE, "wrong history index code")
    _require(history_response.get("metric") == "close", "history metric is not close")
    header = detail_response.get("header")
    _require(isinstance(header, dict) and header.get("code") == CODE, "wrong metadata index code")
    _require(header.get("name") in (INDEX_NAME, "偏债混合型基金指数"), "wrong index name")
    series = history_response.get("series")
    _require(isinstance(series, list) and len(series) >= 2, "missing or insufficient close observations")

    points = {}
    previous = ""
    for row in series:
        _require(isinstance(row, dict), "observation is not an object")
        stamp = row.get("date")
        _day(stamp)
        _require(stamp > previous, "duplicate or unordered dates")
        previous = stamp
        close = _number(row.get("close"))
        value = _number(row.get("value"))
        _require(abs(value - close) <= 1e-9, "chart value and raw close do not align")
        points[stamp] = close

    as_of = max(points)
    today = today or datetime.now(timezone(timedelta(hours=8))).date()
    _require(_day(as_of) <= today, "future observation date")
    _require(detail_response.get("update_time") == as_of, "metadata and history dates do not align")
    _require(abs(_number(history_response.get("latest")) - points[as_of]) <= 1e-9, "history latest does not match last observation")

    fields = detail_response.get("table")
    _require(isinstance(fields, list), "missing latest-close metadata")
    closes = [row.get("latest") for row in fields if isinstance(row, dict) and row.get("name") == "收盘价"]
    _require(len(closes) == 1, "missing or ambiguous latest raw close")
    latest_close = _number(closes[0])
    _require(abs(latest_close - points[as_of]) <= LEVEL_TOLERANCE + 1e-9, "metadata close does not match history")

    return {
        "code": CODE,
        "name": "韭圈儿公开接口 · 万得偏债混合型基金指数（close点位）",
        "url": HISTORY_URL,
        "as_of": as_of,
        "points": points,
        "precision": "raw index close (source values retained)",
        "provider_note": "第三方公开close点位，保留来源精度，不从收益率反推。历史日期曾出现错位，仅可在近期60个共同日期校验后追加新日期，不覆盖已发布历史。",
        "provider_id": "funddb",
        "overlap_tolerance": LEVEL_TOLERANCE,
        "metadata": {
            "index_name": INDEX_NAME,
            "detail_url": DETAIL_URL,
            "metric": "close",
            "first_observation": min(points),
            "latest_close": latest_close,
            "latest_close_date": as_of,
        },
    }


def fetch_wind_backup(fetch_json):
    """Fetch with caller-supplied ``fetch_json(url)``; both requests are GET.

    The callback owns timeouts, bounded retries, HTTP headers and JSON decoding.
    No payload, authorization header, cookie, Referer or secret is required.
    """
    detail = fetch_json(DETAIL_URL)
    history = fetch_json(HISTORY_URL)
    return parse_wind_backup(history, detail)

#!/usr/bin/env python3
"""Refresh raw SSE Composite, spot gold and Hang Seng TECH daily values.

The three calendars are retained separately. No prices are filled, normalized,
rebased, reconstructed, or replaced by another instrument. Each source may
fall back to its own last accepted history when --allow-cached is requested.
"""

from __future__ import annotations

import argparse
import copy
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

START = "2014-12-31"
LAUNCH = "2020-07-27"
DATASET_ID = "sse-gold-hstech"
CHINA = timezone(timedelta(hours=8))
MIN_HISTORY_ROWS = 2500
MAX_BYTES = 16 * 1024 * 1024
TOLERANCE = {"sse": 0.01, "gold": 0.001, "hstech": 0.01}
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "site" / "markets-data.json"
SSE_ENDPOINT = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
SSE_HOLIDAY_URL = "https://www.sse.com.cn/disclosure/announcement/general/c/c_20180606_4567900.shtml"
HSTECH_ENDPOINT = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
HSTECH_IDENTITY_URL = "https://www.hsi.com.hk/content/dam/wpb/hsil/index/methodologies/en_hk/hstech/IM_hstech.pdf"
HSTECH_QUOTE_URL = "https://quote.eastmoney.com/gb/zsHSTECH.html"
GOLD_ENDPOINT = "https://freeserv.dukascopy.com/2.0/index.php"
GOLD_IDENTITY_URL = "https://www.dukascopy.com/swiss/english/cfd/range-of-markets/gold/"
GOLD_CALLBACK = "goldAudit"
GOLD_LIMIT = 5000
GOLD_QUERY = {
    "path": "chart/json3", "instrument": "XAU/USD", "interval": "1DAY",
    "offer_side": "B", "last_update": "1419897600000", "time_direction": "N",
    "limit": str(GOLD_LIMIT), "splits": "true", "stocks": "true", "jsonp": GOLD_CALLBACK,
}
GOLD_URL = GOLD_ENDPOINT + "?" + urlencode(GOLD_QUERY)
CONFIG = {
    "sse": {"code": "000001.SH", "name": "上证指数", "unit": "点", "type": "price_index", "provider_id": "csindex", "source_name": "中证指数公司官网"},
    "gold": {"code": "XAU", "name": "伦敦金现", "unit": "美元/金衡盎司", "type": "spot_bid", "provider_id": "dukascopy_xauusd_bid", "source_name": "Dukascopy XAU/USD 现货买价（日线，UTC）"},
    "hstech": {"code": "HSTECH", "name": "恒生科技指数", "unit": "点", "type": "price_index", "provider_id": "eastmoney_hstech", "source_name": "东方财富 HSTECH 日线"},
}


class MarketDataError(ValueError):
    """A response or previous snapshot cannot safely be used."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MarketDataError(message)


def parse_date(value: Any, *, compact: bool = False) -> str:
    require(isinstance(value, str), "Date must be a string")
    try:
        parsed = datetime.strptime(value, "%Y%m%d" if compact else "%Y-%m-%d").date()
    except ValueError as error:
        raise MarketDataError(f"Invalid date: {value!r}") from error
    expected = parsed.strftime("%Y%m%d") if compact else parsed.isoformat()
    require(value == expected, "Noncanonical date")
    return parsed.isoformat()


def number(value: Any, *, numeric_text: bool = False) -> float:
    valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    if numeric_text:
        valid = valid or (isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?", value) is not None)
    require(valid, "Close is not a number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as error:
        raise MarketDataError("Invalid numeric price") from error
    require(math.isfinite(result) and result > 0, "Nonpositive or nonfinite price")
    return result


def decode_json(raw: bytes) -> Any:
    def invalid_constant(value):
        raise MarketDataError(f"Nonstandard JSON constant: {value}")
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result
    require(len(raw) <= MAX_BYTES, "Source exceeds response size limit")
    try:
        return json.loads(raw.decode("utf-8-sig"), parse_constant=invalid_constant, object_pairs_hook=unique_keys)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise MarketDataError("Invalid JSON response") from error


def fetch_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "CSI300-Wind-Chart/2.0", "Accept": "application/json,text/html"})
    with urlopen(request, timeout=30) as response:
        result = response.read(MAX_BYTES + 1)
    require(len(result) <= MAX_BYTES, "Source exceeds response size limit")
    return result


def completed_date(now: datetime, key: str = "sse") -> date:
    require(now.tzinfo is not None, "Clock must be timezone-aware")
    # UTC gold bars finish at 08:00 Beijing. Before then, today's Beijing
    # calendar date alone must not cause an unfinished gold bar to be accepted.
    zone = timezone.utc if key == "gold" else CHINA
    return now.astimezone(zone).date() - timedelta(days=1)


def sse_url(cutoff: date) -> str:
    return SSE_ENDPOINT + "?" + urlencode({"indexCode": "000001", "startDate": "20141231", "endDate": cutoff.strftime("%Y%m%d")})


def hstech_url(cutoff: date) -> str:
    return HSTECH_ENDPOINT + "?" + urlencode({
        "secid": "124.HSTECH", "klt": "101", "fqt": "0", "beg": "20141231", "end": cutoff.strftime("%Y%m%d"), "lmt": "100000",
        "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    })


def validate_rows(rows: Any, key: str, cutoff: date, *, complete: bool = True) -> None:
    require(isinstance(rows, list) and bool(rows), f"{key}: missing observations")
    last = ""
    counts: dict[int, int] = {}
    for row in rows:
        require(isinstance(row, list) and len(row) == 2, f"{key}: invalid observation shape")
        day = parse_date(row[0])
        require(last < day <= cutoff.isoformat(), f"{key}: duplicate, unordered or incomplete/future date {day}")
        parsed = date.fromisoformat(day)
        require(day >= START, f"{key}: out-of-range observation {day}")
        if key != "gold":
            require(parsed.weekday() < 5, f"{key}: weekend equity observation {day}")
        if key == "sse":
            require(day != "2018-06-18", "sse: audited nontrading placeholder must not be published")
        number(row[1])
        if complete and last:
            require((parsed - date.fromisoformat(last)).days <= 21, f"{key}: unexplained history gap ending {day}")
        counts[parsed.year] = counts.get(parsed.year, 0) + 1
        last = day
    if complete:
        require(rows[0][0] == START, f"{key}: missing observed history start {START}")
        require(len(rows) >= MIN_HISTORY_ROWS, f"{key}: truncated history ({len(rows)} rows)")
        for year in range(2015, date.fromisoformat(last).year):
            require(counts.get(year, 0) >= 200, f"{key}: incomplete year {year}")
    if key == "hstech" and rows[0][0] == START:
        require(rows[0][1] == 3000, "hstech: observed base value is not 3000")


def check_ohlc(record: dict[str, Any], *, numeric_text: bool = False) -> float:
    close = number(record.get("close"), numeric_text=numeric_text)
    low, high, opening = (number(record.get(k), numeric_text=numeric_text) for k in ("low", "high", "open"))
    require(low <= close <= high and low <= opening <= high, "OHLC values are inconsistent")
    return close


def parse_sse(payload: Any, cutoff: date) -> list[list[Any]]:
    require(isinstance(payload, dict) and str(payload.get("code")) == "200", "sse: unsuccessful response")
    require(isinstance(payload.get("data"), list), "sse: missing daily data")
    rows, last_source_date = [], ""
    for item in payload["data"]:
        require(isinstance(item, dict) and item.get("indexCode") == "000001", "sse: wrong instrument identity")
        require(item.get("indexNameCn") == "上证指数" or item.get("indexNameCnAll") == "上证综合指数", "sse: wrong index name")
        day = parse_date(item.get("tradeDate"), compact=True)
        require(day > last_source_date, "sse: unordered or duplicate source dates")
        last_source_date = day
        if day == "2018-06-18":
            # An audited publisher placeholder on the exchange's Dragon Boat
            # Festival closure. Do not turn a carried close into a trading bar.
            require(bool(rows) and rows[-1][0] == "2018-06-15"
                    and all(item.get(k) is None for k in ("open", "high", "low"))
                    and all(type(item.get(k)) in (int, float) and item[k] == 0 for k in ("change", "changePct", "tradingVol", "tradingValue"))
                    and number(item.get("close")) == rows[-1][1],
                    "sse: unexpected form of audited 2018-06-18 nontrading placeholder")
            continue
        rows.append([day, check_ohlc(item)])
    validate_rows(rows, "sse", cutoff, complete=False)
    return rows


def parse_hstech(payload: Any, cutoff: date) -> list[list[Any]]:
    require(isinstance(payload, dict) and payload.get("rc") == 0, "hstech: unsuccessful response")
    body = payload.get("data")
    require(isinstance(body, dict) and body.get("code") == "HSTECH" and body.get("market") == 124 and body.get("name") == "恒生科技指数", "hstech: wrong price-index identity")
    require(body.get("decimal") == 2, "hstech: unexpected precision")
    candles = body.get("klines")
    require(isinstance(candles, list), "hstech: missing daily candles")
    require(isinstance(body.get("dktotal"), int) and len(candles) <= body["dktotal"] < 100000, "hstech: invalid or truncated total")
    rows = []
    for candle in candles:
        require(isinstance(candle, str), "hstech: malformed candle")
        values = candle.split(",")
        require(len(values) >= 5, "hstech: missing candle columns")
        close = check_ohlc(dict(zip(("open", "close", "high", "low"), values[1:5])), numeric_text=True)
        rows.append([parse_date(values[0]), close])
    validate_rows(rows, "hstech", cutoff, complete=False)
    return rows


def validate_gold_url(url: str) -> None:
    parsed = urlsplit(url)
    expected = urlsplit(GOLD_ENDPOINT)
    require((parsed.scheme, parsed.netloc, parsed.path) == (expected.scheme, expected.netloc, expected.path)
            and not parsed.fragment and parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True) == {k: [v] for k, v in GOLD_QUERY.items()},
            "gold: wrong instrument, offer side, interval or provider request")


def parse_gold(raw: bytes, cutoff: date, *, source_url: str = GOLD_URL) -> list[list[Any]]:
    validate_gold_url(source_url)
    require(len(raw) <= MAX_BYTES, "gold: response exceeds size limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as error:
        raise MarketDataError("gold: invalid JSONP encoding") from error
    # This endpoint has no in-body symbol. Pin the entire instrument request,
    # callback and observed historical anchor; never execute the JSONP as JS.
    matched = re.fullmatch(r"\s*" + re.escape(GOLD_CALLBACK) + r"\((\[.*\])\);?\s*", text, re.S)
    require(matched is not None, "gold: unexpected JSONP wrapper")
    items = decode_json(matched[1].encode("utf-8"))
    require(isinstance(items, list) and 0 < len(items) < GOLD_LIMIT, "gold: missing history or response hit pagination limit")
    rows, previous = [], ""
    for item in items:
        require(isinstance(item, list) and len(item) == 6, "gold: malformed daily candle")
        stamp = item[0]
        require(isinstance(stamp, int) and not isinstance(stamp, bool) and 0 < stamp < 4102444800000 and stamp % 86400000 == 0,
                "gold: daily timestamp must be UTC midnight in milliseconds")
        day = datetime.fromtimestamp(stamp / 1000, timezone.utc).date().isoformat()
        require(day > previous, "gold: unordered or duplicate source dates")
        require(day <= (cutoff + timedelta(days=1)).isoformat(), "gold: future source observation")
        previous = day
        close = check_ohlc(dict(zip(("open", "high", "low", "close"), item[1:5])))
        require(all(abs(value - round(value, 3)) < 1e-9 for value in item[1:5]), "gold: unexpected price precision")
        require(isinstance(item[5], (int, float)) and not isinstance(item[5], bool)
                and math.isfinite(item[5]) and item[5] >= 0, "gold: invalid candle volume")
        if START <= day <= cutoff.isoformat():
            rows.append([day, close])
    validate_rows(rows, "gold", cutoff, complete=False)
    if rows[0][0] == START:
        require(abs(rows[0][1] - 1183.701) <= TOLERANCE["gold"] + 1e-9,
                "gold: observed XAU/USD BID historical anchor changed; review required")
    return rows


def load_series(key: str, cutoff: date) -> tuple[list[list[Any]], dict[str, Any]]:
    config = CONFIG[key]
    if key == "sse":
        url = sse_url(cutoff)
        rows = parse_sse(decode_json(fetch_bytes(url)), cutoff)
        source = {"url": url, "excluded_nontrading_dates": ["2018-06-18"], "calendar_source_url": SSE_HOLIDAY_URL}
    elif key == "hstech":
        url = hstech_url(cutoff)
        rows = parse_hstech(decode_json(fetch_bytes(url)), cutoff)
        source = {"url": HSTECH_QUOTE_URL, "data_url": url, "identity_url": HSTECH_IDENTITY_URL}
    else:
        rows = parse_gold(fetch_bytes(GOLD_URL), cutoff)
        source = {"url": GOLD_IDENTITY_URL, "data_url": GOLD_URL, "instrument": "XAU/USD", "offer_side": "BID", "timezone": "UTC"}
    validate_rows(rows, key, cutoff)
    source.update({"provider_id": config["provider_id"], "name": config["source_name"]})
    return rows, source


def validate_source(source: Any, key: str) -> None:
    config = CONFIG[key]
    require(isinstance(source, dict) and source.get("provider_id") == config["provider_id"]
            and source.get("name") == config["source_name"], f"Snapshot: wrong {key} provider")
    require(source.get("mode") in ("live", "cached") and isinstance(source.get("checked_at"), str),
            "Snapshot: invalid source status")
    try:
        checked = datetime.fromisoformat(source["checked_at"].replace("Z", "+00:00"))
    except ValueError as error:
        raise MarketDataError("Snapshot: invalid source check time") from error
    require(checked.tzinfo is not None, "Snapshot: source check time must be timezone-aware")
    if source["mode"] == "cached":
        require(isinstance(source.get("error"), str) and bool(source["error"]), "Snapshot: cached source missing failure reason")
    if key == "gold":
        require(source.get("instrument") == "XAU/USD" and source.get("offer_side") == "BID"
                and source.get("timezone") == "UTC" and source.get("url") == GOLD_IDENTITY_URL,
                "Snapshot: wrong gold instrument, offer side, timezone or provider")
        require(isinstance(source.get("data_url"), str), "Snapshot: missing gold request URL")
        validate_gold_url(source["data_url"])
        return
    url = source.get("url") if key == "sse" else source.get("data_url")
    require(isinstance(url, str), f"Snapshot: missing {key} request URL")
    parsed = urlsplit(url)
    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    date_key = "endDate" if key == "sse" else "end"
    require(isinstance(query.get(date_key), list) and len(query[date_key]) == 1, "Snapshot: missing source request end date")
    end = date.fromisoformat(parse_date(query[date_key][0], compact=True))
    expected = urlsplit(sse_url(end) if key == "sse" else hstech_url(end))
    require((parsed.scheme, parsed.netloc, parsed.path) == (expected.scheme, expected.netloc, expected.path)
            and not parsed.fragment and query == parse_qs(expected.query), f"Snapshot: wrong {key} source request")
    if key == "sse":
        require(source.get("excluded_nontrading_dates") == ["2018-06-18"] and source.get("calendar_source_url") == SSE_HOLIDAY_URL,
                "Snapshot: missing audited nontrading-date exclusion")
    else:
        require(source.get("url") == HSTECH_QUOTE_URL and source.get("identity_url") == HSTECH_IDENTITY_URL,
                "Snapshot: wrong HSTECH provider or methodology link")


def validate_snapshot(snapshot: Any, cutoff: date | dict[str, date]) -> None:
    require(isinstance(snapshot, dict) and snapshot.get("schema_version") == 1 and snapshot.get("dataset_id") == DATASET_ID, "Snapshot: wrong schema or dataset")
    require(snapshot.get("start_date") == START, "Snapshot: wrong start date")
    require(isinstance(snapshot.get("series"), dict) and set(snapshot["series"]) == set(CONFIG), "Snapshot: wrong series")
    require(isinstance(snapshot.get("generated_at"), str), "Snapshot: missing generation timestamp")
    try:
        generated = datetime.fromisoformat(snapshot["generated_at"].replace("Z", "+00:00"))
    except ValueError as error:
        raise MarketDataError("Snapshot: invalid generation timestamp") from error
    require(generated.tzinfo is not None, "Snapshot: timestamp must be timezone-aware")
    for key, config in CONFIG.items():
        item = snapshot["series"][key]
        require(isinstance(item, dict), "Snapshot: invalid series object")
        require(all(item.get(k) == config[k] for k in ("code", "unit", "type")), f"Snapshot: wrong {key} identity or units")
        validate_rows(item.get("data"), key, cutoff[key] if isinstance(cutoff, dict) else cutoff)
        require(item.get("start_date") == START and item.get("as_of") == item["data"][-1][0], f"Snapshot: inconsistent {key} dates")
        validate_source(item.get("source"), key)
        if key == "hstech":
            require(item.get("launch_date") == LAUNCH and item.get("backtest_before") == LAUNCH, "Snapshot: missing pre-launch disclosure")
        if key == "gold":
            require(abs(item["data"][0][1] - 1183.701) <= TOLERANCE["gold"] + 1e-9,
                    "Snapshot: wrong observed gold history start")
    shared = set.intersection(*(set(row[0] for row in item["data"]) for item in snapshot["series"].values()))
    require(bool(shared) and snapshot.get("common_as_of") == max(shared), "Snapshot: wrong common cutoff")
    require(snapshot.get("as_of") == max(item["as_of"] for item in snapshot["series"].values()), "Snapshot: wrong latest cutoff")


def preserve_history(rows: list[list[Any]], previous: dict[str, Any], key: str) -> list[list[Any]]:
    candidate = dict(rows)
    require(rows[-1][0] >= previous["as_of"], f"{key}: source date regressed to {rows[-1][0]}")
    for day, close in previous["data"]:
        require(day in candidate, f"{key}: previously published date missing: {day}")
        require(abs(candidate[day] - close) <= TOLERANCE[key] + 1e-9, f"{key}: historical value changed on {day}; review required")
    return copy.deepcopy(previous["data"]) + [row for row in rows if row[0] > previous["as_of"]]


def atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def update(output: Path, *, previous_path: Path | None = None, allow_cached: bool = False, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cutoffs = {key: completed_date(now, key) for key in CONFIG}
    checked = now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    prior_file = previous_path or (output if output.exists() else None)
    previous = decode_json(prior_file.read_bytes()) if prior_file is not None else None
    if previous is not None:
        validate_snapshot(previous, cutoffs)
    series, warnings = {}, []
    changed = previous is None
    for key, config in CONFIG.items():
        try:
            rows, source = load_series(key, cutoffs[key])
            if previous is not None:
                rows = preserve_history(rows, previous["series"][key], key)
                changed = changed or rows != previous["series"][key]["data"]
            source.update({"mode": "live", "checked_at": checked})
            item = {k: config[k] for k in ("name", "code", "unit", "type")}
            item.update({"start_date": START, "as_of": rows[-1][0], "source": source, "data": rows})
            if key == "hstech":
                item.update({"launch_date": LAUNCH, "backtest_before": LAUNCH})
            if key == "gold":
                item["note"] = "Dukascopy XAU/USD 现货买价（BID），按 UTC 日线保留原始收盘数值及周日部分交易时段；属于供应商的滚动现货金/CFD 报价，非 LBMA 定盘价，与沪港股指收盘时刻不同。"
            series[key] = item
        except (ValueError, TypeError, KeyError, OSError, TimeoutError) as error:
            if not allow_cached or previous is None:
                raise MarketDataError(f"{key}: no usable current source; existing file preserved: {error}") from error
            item = copy.deepcopy(previous["series"][key])
            reason = (str(error) or type(error).__name__)[:400]
            item["source"].update({"mode": "cached", "checked_at": checked, "error": reason})
            series[key] = item
            warnings.append(f"{config['name']} 沿用截至 {item['as_of']} 的已发布数据：{reason}")
    shared = set.intersection(*(set(row[0] for row in item["data"]) for item in series.values()))
    require(bool(shared), "No common date between the three series")
    outcome = "cached" if warnings else "fresh" if changed else "unchanged"
    dataset = {
        "schema_version": 1, "dataset_id": DATASET_ID, "start_date": START,
        "as_of": max(item["as_of"] for item in series.values()), "common_as_of": max(shared),
        "generated_at": checked if changed else previous["generated_at"], "series": series,
        "update_status": {"checked_at": checked, "outcome": outcome, "message": "部分来源未通过检查，保留对应序列的已发布历史。" if warnings else "三项来源检查完成。", "warnings": warnings},
        "notes": ["使用原始每日收盘数值，不归一化、不换算收益率、不补点。", "股指截至上一北京时间自然日，黄金截至上一 UTC 自然日；北京时间每日 08:00 后两者日期上限一致。各市场按实际日期绘制，不虚构休市日价格。", "恒生科技指数 2020-07-27 以前为发布前回溯数据。", "左右轴数值刻度一致，股指单位为点，黄金为美元/金衡盎司；相交不代表等值或等收益。"],
    }
    validate_snapshot(dataset, cutoffs)
    atomic_write(output, dataset)
    return dataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--allow-cached", action="store_true")
    args = parser.parse_args()
    try:
        dataset = update(args.output, previous_path=args.previous, allow_cached=args.allow_cached)
    except (ValueError, TypeError, KeyError, OSError, TimeoutError) as error:
        print(f"Markets update failed; existing file preserved: {error}")
        return 1
    print(f"Saved three absolute-value histories through {dataset['as_of']}; latest common date {dataset['common_as_of']}; status {dataset['update_status']['outcome']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

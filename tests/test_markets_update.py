"""Source contracts and preservation checks for the three raw-value histories.

Short parser examples below are archived public observations. Long histories
used to exercise update transactions are explicitly synthetic test data, not
financial observations or fixtures offered for publication.
"""

import copy
from datetime import date, datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.error import URLError
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("update_markets", ROOT / "scripts" / "update_markets.py")
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)
NOW = datetime(2026, 11, 28, 4, tzinfo=timezone.utc)
NEXT_NOW = datetime(2026, 12, 1, 0, tzinfo=timezone.utc)
CUTOFF = date(2026, 11, 27)

# Source: CSIndex official 000001 history, retrieved 2026-11-28.
SSE_SAMPLE = {"code": "200", "data": [
    {"tradeDate": "20141231", "indexCode": "000001", "indexNameCn": "上证指数", "indexNameCnAll": "上证综合指数",
     "open": 3172.6, "high": 3239.36, "low": 3157.26, "close": 3234.68},
    {"tradeDate": "20150105", "indexCode": "000001", "indexNameCn": "上证指数", "indexNameCnAll": "上证综合指数",
     "open": 3258.63, "high": 3369.28, "low": 3253.88, "close": 3350.52},
]}

# Source: Eastmoney secid=124.HSTECH, retrieved 2026-11-28.
HSTECH_SAMPLE = {"rc": 0, "data": {
    "code": "HSTECH", "market": 124, "name": "恒生科技指数", "decimal": 2, "dktotal": 2931,
    "klines": [
        "2014-12-31,3000.00,3000.00,3000.00,3000.00,0,0.00,0.00,0.00,0.00,0.00",
        "2015-01-02,3006.61,3006.61,3006.61,3006.61,0,0.00,0.00,0.22,6.61,0.00",
    ],
}}

# Source: Dukascopy chart/json3, XAU/USD, 1DAY, offer_side=B, UTC.
GOLD_SAMPLE = [
    [1419897600000, 1181.749, 1210.465, 1180.669, 1199.322, 47.68],
    [1419984000000, 1199.429, 1203.772, 1179.588, 1183.701, 22.25],
    [1420070400000, 1183.949, 1187.403, 1183.230, 1186.664, 0.09],
    [1420156800000, 1186.684, 1194.998, 1167.430, 1188.281, 37.29],
    [1420329600000, 1188.111, 1188.480, 1177.929, 1183.841, 0.69],
    [1420416000000, 1183.841, 1207.387, 1182.784, 1203.241, 45.65],
]


def gold_jsonp(candles):
    return (updater.GOLD_CALLBACK + "(" + json.dumps(candles) + ");").encode()


def synthetic_histories(end=CUTOFF):
    """Dense test calendars with artificial prices, never production data."""
    result = {key: [] for key in updater.CONFIG}
    current = date.fromisoformat(updater.START)
    bases = {"sse": 3234.68, "gold": 1183.701, "hstech": 3000.0}
    while current <= end:
        for key, rows in result.items():
            if key == "gold":
                allowed = current.weekday() != 5
            else:
                allowed = current.weekday() < 5 and not (key == "sse" and current.isoformat() == "2018-06-18")
            if allowed:
                value = bases[key] + len(rows) * (0.017 if key == "gold" else 0.01)
                rows.append([current.isoformat(), round(value, 3 if key == "gold" else 2)])
        current += timedelta(days=1)
    return result


def source_metadata(key, cutoff):
    config = updater.CONFIG[key]
    source = {"provider_id": config["provider_id"], "name": config["source_name"]}
    if key == "sse":
        source.update({"url": updater.sse_url(cutoff), "excluded_nontrading_dates": ["2018-06-18"], "calendar_source_url": updater.SSE_HOLIDAY_URL})
    elif key == "hstech":
        source.update({"url": updater.HSTECH_QUOTE_URL, "data_url": updater.hstech_url(cutoff), "identity_url": updater.HSTECH_IDENTITY_URL})
    else:
        source.update({"url": updater.GOLD_IDENTITY_URL, "data_url": updater.GOLD_URL,
                       "instrument": "XAU/USD", "offer_side": "BID", "timezone": "UTC"})
    return source


class ParserTests(unittest.TestCase):
    def test_archived_raw_closes_are_not_normalized_or_rounded(self):
        self.assertEqual(updater.parse_sse(SSE_SAMPLE, CUTOFF), [["2014-12-31", 3234.68], ["2015-01-05", 3350.52]])
        self.assertEqual(updater.parse_hstech(HSTECH_SAMPLE, CUTOFF), [["2014-12-31", 3000.0], ["2015-01-02", 3006.61]])
        gold = updater.parse_gold(gold_jsonp(GOLD_SAMPLE), CUTOFF)
        self.assertEqual(gold[0], ["2014-12-31", 1183.701])
        self.assertIn(["2015-01-04", 1183.841], gold)  # Real Sunday partial session.
        self.assertNotIn("2014-12-30", {row[0] for row in gold})

    def test_sse_and_hstech_identity_guards(self):
        sse = copy.deepcopy(SSE_SAMPLE)
        sse["data"][0]["indexCode"] = "000300"
        with self.assertRaisesRegex(updater.MarketDataError, "identity"):
            updater.parse_sse(sse, CUTOFF)
        for field, value in (("code", "HSTECHT"), ("market", 100), ("name", "恒生指数"), ("decimal", 3)):
            with self.subTest(field=field):
                payload = copy.deepcopy(HSTECH_SAMPLE)
                payload["data"][field] = value
                with self.assertRaises(updater.MarketDataError):
                    updater.parse_hstech(payload, CUTOFF)
        payload = copy.deepcopy(HSTECH_SAMPLE)
        payload["data"]["klines"][0] = payload["data"]["klines"][0].replace("3000.00", "3001.00")
        with self.assertRaisesRegex(updater.MarketDataError, "base value"):
            updater.parse_hstech(payload, CUTOFF)

    def test_audited_sse_holiday_placeholder_only_is_excluded(self):
        prior = copy.deepcopy(SSE_SAMPLE["data"][0])
        prior.update({"tradeDate": "20180615", "open": 3037.45, "high": 3048.8, "low": 3008.73, "close": 3021.9})
        holiday = copy.deepcopy(prior)
        holiday.update({"tradeDate": "20180618", "open": None, "high": None, "low": None,
                        "change": 0, "changePct": 0, "tradingVol": 0.0, "tradingValue": 0.0})
        payload = {"code": "200", "data": [prior, holiday]}
        self.assertEqual(updater.parse_sse(payload, CUTOFF), [["2018-06-15", 3021.9]])
        for field, value in (("close", 3022), ("tradingVol", 1), ("low", 3000), ("changePct", 0.1), ("tradingValue", False)):
            with self.subTest(field=field):
                bad = copy.deepcopy(payload)
                bad["data"][1][field] = value
                with self.assertRaisesRegex(updater.MarketDataError, "placeholder"):
                    updater.parse_sse(bad, CUTOFF)
        bad = copy.deepcopy(payload)
        bad["data"][1]["tradeDate"] = "20180619"
        with self.assertRaises(updater.MarketDataError):
            updater.parse_sse(bad, CUTOFF)
        bad = copy.deepcopy(payload)
        bad["data"].append(copy.deepcopy(holiday))
        with self.assertRaisesRegex(updater.MarketDataError, "duplicate"):
            updater.parse_sse(bad, CUTOFF)

    def test_gold_request_identity_cannot_switch_to_ask_or_another_symbol(self):
        for field, value in (("instrument", "XAG/USD"), ("offer_side", "A"), ("interval", "1HOUR"), ("jsonp", "other")):
            with self.subTest(field=field):
                query = dict(updater.GOLD_QUERY)
                query[field] = value
                with self.assertRaisesRegex(updater.MarketDataError, "wrong instrument"):
                    updater.parse_gold(gold_jsonp(GOLD_SAMPLE), CUTOFF, source_url=updater.GOLD_ENDPOINT + "?" + urlencode(query))
        with self.assertRaises(updater.MarketDataError):
            updater.parse_gold(gold_jsonp(GOLD_SAMPLE), CUTOFF, source_url=updater.GOLD_URL.replace("freeserv.dukascopy.com", "example.org"))

    def test_jsonp_is_data_only_and_json_is_strict(self):
        raw = gold_jsonp(GOLD_SAMPLE)
        for bad in (raw + b"globalThis.compromised=true;", b"eval(" + raw + b")", raw.replace(b"goldAudit", b"unexpected", 1)):
            with self.subTest(payload=bad[:24]), self.assertRaisesRegex(updater.MarketDataError, "wrapper"):
                updater.parse_gold(bad, CUTOFF)
        for bad in (b'{"x":1,"x":2}', b'{"close":NaN}', b'{"close":Infinity}', b'\xff'):
            with self.subTest(payload=bad), self.assertRaises(updater.MarketDataError):
                updater.decode_json(bad)

    def test_gold_ohlc_precision_utc_and_anchor_guards(self):
        for field, value in ((0, 1419984000001), (1, 1170), (2, 1180), (4, 1183.7011), (5, -1)):
            with self.subTest(field=field):
                bad = copy.deepcopy(GOLD_SAMPLE)
                bad[1][field] = value
                with self.assertRaises(updater.MarketDataError):
                    updater.parse_gold(gold_jsonp(bad), CUTOFF)
        bad = copy.deepcopy(GOLD_SAMPLE)
        bad[1][4] = 1184
        with self.assertRaisesRegex(updater.MarketDataError, "anchor"):
            updater.parse_gold(gold_jsonp(bad), CUTOFF)
        # This is the rejected Sina 2014-12-31 bar; its open is below its low.
        with self.assertRaisesRegex(updater.MarketDataError, "OHLC"):
            updater.check_ohlc({"open": 1181.110, "high": 1204.240, "low": 1182.544, "close": 1193.250})

    def test_unfinished_gold_bar_is_excluded_without_relabeling(self):
        cutoff = date(2015, 1, 4)
        rows = updater.parse_gold(gold_jsonp(GOLD_SAMPLE), cutoff)
        self.assertEqual(rows[-1], ["2015-01-04", 1183.841])
        self.assertNotIn("2015-01-05", {row[0] for row in rows})
        with self.assertRaisesRegex(updater.MarketDataError, "future"):
            updater.parse_gold(gold_jsonp(GOLD_SAMPLE), date(2015, 1, 3))

    def test_date_order_positive_prices_and_equity_weekends(self):
        for rows in (
            [["2014-12-31", 3000], ["2014-12-31", 3000]],
            [["2015-01-02", 3001], ["2014-12-31", 3000]],
            [["2015-01-03", 3000]],
            [["2015-1-2", 3000]],
            [["2026-11-30", 3000]],
            [["2014-12-31", 0]],
            [["2014-12-31", True]],
            [["2014-12-31", float("inf")]],
        ):
            with self.subTest(rows=rows), self.assertRaises(updater.MarketDataError):
                updater.validate_rows(rows, "sse", CUTOFF, complete=False)
        with self.assertRaisesRegex(updater.MarketDataError, "truncated"):
            updater.validate_rows([["2014-12-31", 3000]], "hstech", CUTOFF)

    def test_response_limit_and_timeout_are_enforced(self):
        response = io.BytesIO(b"123456789")
        with mock.patch.object(updater, "MAX_BYTES", 8), mock.patch.object(updater, "urlopen", return_value=response) as opening:
            with self.assertRaisesRegex(updater.MarketDataError, "size limit"):
                updater.fetch_bytes(updater.GOLD_URL)
            self.assertEqual(opening.call_args.kwargs["timeout"], 30)

    def test_calendar_cutoffs_match_completed_daily_bars(self):
        before_midnight_china = datetime(2026, 11, 27, 15, 59, tzinfo=timezone.utc)
        after_midnight_china = datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc)
        self.assertEqual(updater.completed_date(before_midnight_china, "sse"), date(2026, 11, 26))
        self.assertEqual(updater.completed_date(after_midnight_china, "sse"), date(2026, 11, 27))
        self.assertEqual(updater.completed_date(after_midnight_china, "gold"), date(2026, 11, 26))
        for key in updater.CONFIG:
            self.assertEqual(updater.completed_date(datetime(2026, 11, 28, 0, tzinfo=timezone.utc), key), date(2026, 11, 27))


class UpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.histories = synthetic_histories()
        cls.extended = synthetic_histories(date(2026, 11, 30))
        with tempfile.TemporaryDirectory() as directory:
            def loader(key, cutoff):
                return copy.deepcopy(cls.histories[key]), source_metadata(key, cutoff)
            with mock.patch.object(updater, "load_series", side_effect=loader):
                cls.snapshot = updater.update(Path(directory) / "markets.json", now=NOW)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "markets-data.json"
        self.output.write_text(json.dumps(self.snapshot), encoding="utf-8")
        self.original_bytes = self.output.read_bytes()

    def run_update(self, histories=None, failures=None, *, now=NEXT_NOW, allow_cached=True):
        histories = self.extended if histories is None else histories
        failures = failures or {}
        def loader(key, cutoff):
            if key in failures:
                raise failures[key]
            return copy.deepcopy(histories[key]), source_metadata(key, cutoff)
        with mock.patch.object(updater, "load_series", side_effect=loader):
            return updater.update(self.output, now=now, allow_cached=allow_cached)

    def test_bootstrap_and_append_keep_absolute_values_and_backtest_disclosure(self):
        result = self.run_update()
        self.assertEqual(result["update_status"]["outcome"], "fresh")
        self.assertEqual(result["as_of"], "2026-11-30")
        self.assertEqual(result["common_as_of"], "2026-11-30")
        for key in updater.CONFIG:
            self.assertEqual(result["series"][key]["data"], self.extended[key])
        self.assertEqual(result["series"]["hstech"]["backtest_before"], "2020-07-27")
        self.assertEqual(result["series"]["gold"]["source"]["offer_side"], "BID")
        self.assertEqual(result["series"]["gold"]["data"][0][1], 1183.701)

    def test_weekend_or_holiday_unchanged_is_successful_and_keeps_generation_time(self):
        result = self.run_update(self.histories, now=datetime(2026, 11, 29, 4, tzinfo=timezone.utc))
        self.assertEqual(result["update_status"]["outcome"], "unchanged")
        self.assertEqual(result["generated_at"], self.snapshot["generated_at"])
        self.assertEqual(result["as_of"], "2026-11-27")
        self.assertEqual(result["update_status"]["warnings"], [])
        for key in updater.CONFIG:
            self.assertEqual(result["series"][key]["data"], self.histories[key])

    def test_partial_outage_preserves_failed_series_and_independent_dates(self):
        result = self.run_update(failures={"gold": URLError("HTTP 429 Too Many Requests")})
        self.assertEqual(result["update_status"]["outcome"], "cached")
        self.assertEqual(result["series"]["gold"]["data"], self.histories["gold"])
        self.assertEqual(result["series"]["gold"]["as_of"], "2026-11-27")
        self.assertEqual(result["series"]["sse"]["as_of"], "2026-11-30")
        self.assertEqual(result["as_of"], "2026-11-30")
        self.assertEqual(result["common_as_of"], "2026-11-27")
        self.assertIn("429", result["series"]["gold"]["source"]["error"])
        self.assertEqual(result["series"]["gold"]["source"]["mode"], "cached")
        self.assertNotIn("2026-11-30", {row[0] for row in result["series"]["gold"]["data"]})

    def test_all_sources_down_keep_last_data_and_exact_reasons(self):
        result = self.run_update(failures={key: TimeoutError(key + " timed out") for key in updater.CONFIG})
        self.assertEqual(result["generated_at"], self.snapshot["generated_at"])
        self.assertEqual(result["as_of"], self.snapshot["as_of"])
        self.assertEqual(len(result["update_status"]["warnings"]), 3)
        for key in updater.CONFIG:
            self.assertEqual(result["series"][key]["data"], self.histories[key])
            self.assertIn(key + " timed out", result["series"][key]["source"]["error"])

    def test_historical_revision_is_cached_without_overwriting_published_values(self):
        histories = copy.deepcopy(self.extended)
        histories["gold"][100][1] += 0.002
        result = self.run_update(histories)
        self.assertEqual(result["series"]["gold"]["data"], self.histories["gold"])
        self.assertIn("historical value changed", result["series"]["gold"]["source"]["error"])
        self.assertEqual(result["series"]["hstech"]["as_of"], "2026-11-30")

    def test_within_precision_differences_never_rewrite_published_observations(self):
        histories = copy.deepcopy(self.extended)
        histories["gold"][100][1] = round(histories["gold"][100][1] + 0.001, 3)
        histories["sse"][100][1] = round(histories["sse"][100][1] + 0.01, 2)
        result = self.run_update(histories)
        self.assertEqual(result["update_status"]["outcome"], "fresh")
        for key in ("gold", "sse"):
            self.assertEqual(result["series"][key]["data"][:len(self.histories[key])], self.histories[key])

    def test_rollback_and_missing_published_date_are_not_accepted(self):
        for kind in ("rollback", "missing"):
            with self.subTest(kind=kind):
                histories = copy.deepcopy(self.extended)
                if kind == "rollback":
                    histories["hstech"] = self.histories["hstech"][:-1]
                else:
                    del histories["hstech"][100]
                result = self.run_update(histories)
                self.assertEqual(result["series"]["hstech"]["data"], self.histories["hstech"])
                self.assertIn("regressed" if kind == "rollback" else "missing", result["series"]["hstech"]["source"]["error"])

    def test_strict_mode_failure_preserves_entire_existing_file(self):
        with self.assertRaisesRegex(updater.MarketDataError, "existing file preserved"):
            self.run_update(failures={"gold": URLError("source unavailable")}, allow_cached=False)
        self.assertEqual(self.output.read_bytes(), self.original_bytes)

    def test_missing_explicit_previous_file_is_not_silently_bootstrapped(self):
        with mock.patch.object(updater, "load_series") as loading, self.assertRaises(FileNotFoundError):
            updater.update(self.output, previous_path=Path(self.directory.name) / "missing.json", now=NOW)
        loading.assert_not_called()
        self.assertEqual(self.output.read_bytes(), self.original_bytes)

    def test_invalid_previous_source_identity_rejects_before_fetch(self):
        for mutation in ("provider", "ask", "instrument", "timezone", "source_url", "request", "unit", "baseline"):
            with self.subTest(mutation=mutation):
                prior = copy.deepcopy(self.snapshot)
                item = prior["series"]["gold"]
                if mutation == "provider":
                    item["source"]["provider_id"] = "sina_xau"
                elif mutation == "ask":
                    item["source"]["offer_side"] = "ASK"
                elif mutation == "instrument":
                    item["source"]["instrument"] = "XAU/EUR"
                elif mutation == "timezone":
                    item["source"]["timezone"] = "Asia/Shanghai"
                elif mutation == "source_url":
                    item["source"]["url"] = "https://example.org/gold"
                elif mutation == "request":
                    item["source"]["data_url"] = updater.GOLD_URL.replace("offer_side=B", "offer_side=A")
                elif mutation == "unit":
                    item["unit"] = "人民币/克"
                else:
                    item["data"][0][1] = 1193.25
                self.output.write_text(json.dumps(prior), encoding="utf-8")
                original = self.output.read_bytes()
                with mock.patch.object(updater, "load_series") as loading, self.assertRaises(updater.MarketDataError):
                    updater.update(self.output, now=NOW, allow_cached=True)
                loading.assert_not_called()
                self.assertEqual(self.output.read_bytes(), original)

    def test_atomic_write_failure_leaves_original_and_cleans_temporary_file(self):
        with mock.patch.object(updater.os, "replace", side_effect=OSError("disk replace failed")):
            with self.assertRaisesRegex(OSError, "disk replace failed"):
                self.run_update()
        self.assertEqual(self.output.read_bytes(), self.original_bytes)
        self.assertEqual(list(self.output.parent.glob(".markets-data.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()

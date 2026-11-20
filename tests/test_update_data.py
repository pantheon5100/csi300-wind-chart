"""Integrity and failure-path checks using archived public source observations."""

import copy
from contextlib import redirect_stderr
from datetime import datetime, timezone
import gzip
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.error import URLError


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("update_data", ROOT / "scripts" / "update_data.py")
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)
NOW = datetime(2026, 11, 20, 4, 0, tzinfo=timezone.utc)


class UpdateDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures = ROOT / "tests" / "fixtures"
        cls.stock = json.loads(gzip.decompress((fixtures / "stock.json.gz").read_bytes()))
        cls.wind = json.loads(gzip.decompress((fixtures / "wind.json.gz").read_bytes()))
        cls.snapshot = updater.build_dataset(cls.stock, cls.wind, generated_at=NOW)

    def build(self, stock=None, wind=None, previous=None):
        return updater.build_dataset(
            self.stock if stock is None else stock,
            self.wind if wind is None else wind,
            previous=previous,
            generated_at=NOW,
        )

    def test_archived_sources_baseline_returns_and_common_dates(self):
        result = self.snapshot
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["generated_at"], "2026-11-20T04:00:00Z")
        self.assertEqual(result["as_of"], "2026-11-19")
        self.assertEqual(len(result["data"]), 5314)
        self.assertEqual(result["data"][0], ["2004-12-31", 1000.0, 989.3, 0.0, 0.0])
        self.assertEqual(result["data"][-1][:3], ["2026-11-19", 6001.04, 4976.58])
        self.assertAlmostEqual(result["data"][-1][3], 500.104, places=6)
        self.assertAlmostEqual(result["data"][-1][4], (4976.58 / 989.3 - 1) * 100, places=6)
        self.assertTrue(result["baseline"]["wind_approximate"])
        self.assertIn("第三方", result["sources"]["885003.WI"]["name"])
        self.assertEqual(result["sources"]["885003.WI"]["provider_note"], self.wind["source"])
        # The official stock payload includes this unmatched holiday. Do not fill it.
        self.assertNotIn("2018-06-18", {row[0] for row in result["data"]})

    def test_wrong_indices_and_changed_known_anchor_are_rejected(self):
        bad_stock = copy.deepcopy(self.stock)
        bad_stock["data"][10]["indexCode"] = "000300"
        with self.assertRaisesRegex(updater.DataValidationError, "identity"):
            self.build(stock=bad_stock)
        for mutation in ("key", "name", "anchor"):
            with self.subTest(mutation=mutation):
                bad = copy.deepcopy(self.wind)
                series = bad["series"]["885003.WI"]
                if mutation == "key":
                    bad["series"]["930897"] = bad["series"].pop("885003.WI")
                elif mutation == "name":
                    series["name"] = "中证偏债混合型基金指数"
                else:
                    series["values"][series["dates"].index("2026-11-13")] = 2920.6
                with self.assertRaises(updater.DataValidationError):
                    self.build(wind=bad)

    def test_corrupted_values_and_misaligned_source_arrays_are_rejected(self):
        for value in (None, 0, -1, True, "4976.58", float("nan"), float("inf")):
            with self.subTest(value=value):
                bad = copy.deepcopy(self.wind)
                bad["series"]["885003.WI"]["values"][100] = value
                with self.assertRaises(updater.DataValidationError):
                    self.build(wind=bad)
        bad = copy.deepcopy(self.wind)
        bad["series"]["885003.WI"]["values"].pop()
        with self.assertRaisesRegex(updater.DataValidationError, "lengths"):
            self.build(wind=bad)

    def test_duplicate_noncanonical_future_and_weekend_dates_are_rejected(self):
        for invalid in ("2005-01-04", "2005-1-5", "2026-11-21", "2005-01-08"):
            with self.subTest(invalid=invalid):
                bad = copy.deepcopy(self.wind)
                bad["series"]["885003.WI"]["dates"][1] = invalid
                with self.assertRaises(updater.DataValidationError):
                    self.build(wind=bad)

    def test_truncated_history_and_large_interior_gaps_are_rejected(self):
        for mode in ("recent_only", "missing_start", "missing_year"):
            with self.subTest(mode=mode):
                bad = copy.deepcopy(self.wind)
                series = bad["series"]["885003.WI"]
                pairs = list(zip(series["dates"], series["values"]))
                if mode == "recent_only":
                    pairs = pairs[-1000:]
                elif mode == "missing_start":
                    pairs = pairs[1:]
                else:
                    pairs = [(day, value) for day, value in pairs if not day.startswith("2015-")]
                series["dates"], series["values"] = map(list, zip(*pairs))
                with self.assertRaises(updater.DataValidationError):
                    self.build(wind=bad)

    def test_excess_unshared_dates_fail_instead_of_silently_shortening_history(self):
        bad = copy.deepcopy(self.wind)
        series = bad["series"]["885003.WI"]
        for index in reversed([100, 200, 300, 400, 500, 600]):
            series["dates"].pop(index)
            series["values"].pop(index)
        with self.assertRaisesRegex(updater.DataValidationError, "cannot be aligned"):
            self.build(wind=bad)

    def test_stock_baseline_must_be_observed_and_equal_1000(self):
        bad = copy.deepcopy(self.stock)
        bad["data"][0]["close"] = 1000.01
        with self.assertRaisesRegex(updater.DataValidationError, "baseline"):
            self.build(stock=bad)

    def test_previous_snapshot_blocks_regression_missing_dates_and_history_replacement(self):
        for mode in ("last_date", "interior_date", "historical_value"):
            with self.subTest(mode=mode):
                bad = copy.deepcopy(self.wind)
                series = bad["series"]["885003.WI"]
                if mode == "historical_value":
                    series["values"][300] += 100
                else:
                    index = -1 if mode == "last_date" else 300
                    series["dates"].pop(index)
                    series["values"].pop(index)
                with self.assertRaises(updater.DataValidationError):
                    self.build(wind=bad, previous=self.snapshot)
        self.assertEqual(self.build(previous=self.snapshot)["data"], self.snapshot["data"])

    def test_malformed_previous_snapshot_cannot_disable_guards(self):
        for mutation in ("baseline", "return", "as_of"):
            with self.subTest(mutation=mutation):
                previous = copy.deepcopy(self.snapshot)
                if mutation == "baseline":
                    previous["baseline"]["885003.WI"] = 1000
                elif mutation == "return":
                    previous["data"][100][4] += 1
                else:
                    previous["as_of"] = "2026-11-18"
                with self.assertRaises(updater.DataValidationError):
                    self.build(previous=previous)

    def test_failed_validation_and_failed_atomic_replace_preserve_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "data.json"
            output.write_text(json.dumps(self.snapshot), encoding="utf-8")
            original = output.read_bytes()
            bad = copy.deepcopy(self.stock)
            bad["data"][100]["close"] = None
            with self.assertRaises(updater.DataValidationError):
                updater.update_from_payloads(output, bad, self.wind, generated_at=NOW)
            self.assertEqual(output.read_bytes(), original)
            with mock.patch.object(updater.os, "replace", side_effect=OSError("simulated filesystem error")):
                with self.assertRaises(OSError):
                    updater.update_from_payloads(output, self.stock, self.wind, generated_at=NOW)
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_network_failure_does_not_touch_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "data.json"
            output.write_text(json.dumps(self.snapshot), encoding="utf-8")
            original = output.read_bytes()
            with mock.patch.object(updater, "fetch_json", side_effect=URLError("offline")), mock.patch.object(updater, "fetch_csi_backup", side_effect=URLError("offline")), mock.patch.object(updater, "fetch_wind_backup", side_effect=URLError("offline")), redirect_stderr(io.StringIO()):
                result = updater.main(["--output", str(output)])
            self.assertEqual(result, 1)
            self.assertEqual(output.read_bytes(), original)

    def test_json_duplicate_keys_and_nonfinite_constants_are_rejected(self):
        for raw in (b'{"series": {}, "series": {}}', b'{"value": NaN}'):
            with self.subTest(raw=raw):
                with self.assertRaises(updater.DataValidationError):
                    updater.decode_json(raw, "test")


if __name__ == "__main__":
    unittest.main()

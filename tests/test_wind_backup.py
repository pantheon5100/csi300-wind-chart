"""Identity, raw-value and date-alignment checks for the FundDB GET adapter."""

import copy
from datetime import date
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("wind_backup", ROOT / "scripts" / "wind_backup.py")
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)
TODAY = date(2026, 11, 20)


def observations():
    """Compact real observations from the anonymous API capture on 2026-11-20."""
    history = {
        "code": "885003.WI", "metric": "close", "latest": 4990.73, "years2": 10,
        "series": [
            {"date": "2016-11-21", "value": 3436.72, "close": 3436.72},
            {"date": "2025-11-14", "value": 5028.1854, "close": 5028.1854},
            {"date": "2026-11-12", "value": 5011.27, "close": 5011.27},
            {"date": "2026-11-13", "value": 4990.73, "close": 4990.73},
        ],
    }
    detail = {
        "header": {"code": "885003.WI", "name": "偏债混合型基金指数", "metric": "pe"},
        "update_time": "2026-11-13",
        "table": [{"name": "收盘价", "latest": 4990.73}],
    }
    return history, detail


class WindBackupTests(unittest.TestCase):
    def parse(self, history=None, detail=None):
        initial_history, initial_detail = observations()
        return backup.parse_wind_backup(initial_history if history is None else history, initial_detail if detail is None else detail, today=TODAY)

    def test_real_close_values_are_retained_without_rounding_or_rebasing(self):
        result = self.parse()
        self.assertEqual(result["code"], "885003.WI")
        self.assertEqual(result["points"]["2016-11-21"], 3436.72)
        self.assertEqual(result["points"]["2025-11-14"], 5028.1854)
        self.assertEqual(result["points"]["2026-11-13"], 4990.73)
        self.assertNotIn("2004-12-31", result["points"])
        self.assertEqual(result["overlap_tolerance"], 0.01)
        self.assertIn("raw", result["precision"])
        self.assertEqual(result["as_of"], "2026-11-13")

    def test_substitute_history_metadata_code_and_name_are_rejected(self):
        for mode in ("history_code", "metadata_code", "name", "metric"):
            with self.subTest(mode=mode):
                history, detail = observations()
                if mode == "history_code": history["code"] = "930897"
                elif mode == "metadata_code": detail["header"]["code"] = "885001.WI"
                elif mode == "name": detail["header"]["name"] = "中证偏债混合型基金指数"
                else: history["metric"] = "pe"
                with self.assertRaises(backup.WindBackupError): self.parse(history, detail)

    def test_missing_close_or_value_and_normalized_values_are_rejected(self):
        for mode in ("missing_close", "missing_value", "normalized_value", "truncated"):
            with self.subTest(mode=mode):
                history, _ = observations()
                if mode == "missing_close": history["series"][1].pop("close")
                elif mode == "missing_value": history["series"][1].pop("value")
                elif mode == "normalized_value": history["series"][1]["value"] = 146.31
                else: history["series"] = history["series"][-1:]
                with self.assertRaises(backup.WindBackupError): self.parse(history=history)

    def test_duplicate_unordered_noncanonical_and_future_dates_are_rejected(self):
        for mode in ("duplicate", "order", "invalid", "noncanonical", "future"):
            with self.subTest(mode=mode):
                history, _ = observations()
                if mode == "duplicate": history["series"][1]["date"] = "2016-11-21"
                elif mode == "order": history["series"][1:3] = reversed(history["series"][1:3])
                elif mode == "invalid": history["series"][1]["date"] = "2025-02-30"
                elif mode == "noncanonical": history["series"][1]["date"] = "2025-1-4"
                else: history["series"][-1]["date"] = "2026-12-31"
                with self.assertRaises(backup.WindBackupError): self.parse(history=history)

    def test_nonfinite_nonpositive_and_nonnumeric_values_are_rejected(self):
        for value in (None, True, "4990.73", float("nan"), float("inf"), 0, -1):
            with self.subTest(value=value):
                history, _ = observations()
                history["series"][1]["close"] = value
                with self.assertRaises(backup.WindBackupError): self.parse(history=history)

    def test_same_date_latest_values_must_agree_without_loose_stitching(self):
        history, detail = observations()
        history["latest"] = 4990.72
        with self.assertRaisesRegex(backup.WindBackupError, "history latest"): self.parse(history, detail)
        history, detail = observations()
        detail["table"][0]["latest"] = 4990.75
        with self.assertRaisesRegex(backup.WindBackupError, "metadata close"): self.parse(history, detail)
        detail["table"][0]["latest"] = 4990.735
        self.assertEqual(self.parse(history, detail)["points"]["2026-11-13"], 4990.73)

    def test_metadata_date_and_unique_latest_close_are_required(self):
        for mode in ("date", "missing_close", "ambiguous_close"):
            with self.subTest(mode=mode):
                _, detail = observations()
                if mode == "date": detail["update_time"] = "2026-11-12"
                elif mode == "missing_close": detail["table"] = []
                else: detail["table"].append(copy.deepcopy(detail["table"][0]))
                with self.assertRaises(backup.WindBackupError): self.parse(detail=detail)

    def test_fetch_callback_uses_only_get_urls_and_exact_close_metric(self):
        history, detail = observations()
        calls = []
        def fetch_json(url):
            calls.append(url)
            return detail if url == backup.DETAIL_URL else history
        result = backup.fetch_wind_backup(fetch_json)
        self.assertEqual(result["provider_id"], "funddb")
        self.assertEqual(calls, [backup.DETAIL_URL, backup.HISTORY_URL])
        self.assertIn("code=885003.WI&metric=close&years2=10", calls[1])


if __name__ == "__main__":
    unittest.main()

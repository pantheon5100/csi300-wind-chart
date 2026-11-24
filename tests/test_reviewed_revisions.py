"""Only the three reviewed corrections may bypass historical immutability."""

import copy
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.error import URLError


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("revision_updater", ROOT / "scripts" / "update_data.py")
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)
NOW = datetime(2026, 11, 24, 4, 0, tzinfo=timezone.utc)
CHANGES = [
    {"date": "2026-11-18", "old": 4973.26, "new": 4968.32},
    {"date": "2026-11-19", "old": 4976.58, "new": 4963.24},
    {"date": "2026-11-20", "old": 4979.57, "new": 4940.44},
]


class ReviewedRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = ROOT / "tests" / "fixtures"
        cls.stock = json.loads(gzip.decompress((folder / "stock.json.gz").read_bytes()))
        cls.wind = json.loads(gzip.decompress((folder / "wind.json.gz").read_bytes()))
        # Synthetic trusted metadata is solely for constructing the old test
        # snapshot. Keep the real archived gzip bytes unchanged and test their
        # ambiguous provenance separately in test_update_data.py.
        cls.wind["source"] = "Synthetic trusted Wind 885003.WI metadata for unit tests only"
        cls.stock["data"].append({"indexCode": "H00300", "tradeDate": "20261120", "close": 6010.0})
        series = cls.wind["series"]["885003.WI"]
        series["dates"].append("2026-11-20")
        series["values"].append(4979.57)
        cls.snapshot = updater.build_dataset(cls.stock, cls.wind, generated_at=NOW)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "data.json"
        self.before = copy.deepcopy(self.snapshot)
        self.stock_payload = copy.deepcopy(self.stock)
        self.stock_payload["data"].append({"indexCode": "H00300", "tradeDate": "20261123", "close": 6020.0})
        self.wind_payload = copy.deepcopy(self.wind)
        self.wind_payload["source"] = "Wind AIFin 不可用，本轮为中证官网降级源（中证官网H20269(同口径)）"
        series = self.wind_payload["series"]["885003.WI"]
        for change in CHANGES:
            series["values"][series["dates"].index(change["date"])] = change["new"]
        series["dates"].append("2026-11-23")
        series["values"].append(4950.07)
        provider = updater.PROVIDERS["885003.WI"][1]
        self.funddb = {
            "code": "885003.WI", "name": provider["name"], "provider_id": provider["provider_id"],
            "url": provider["url"], "precision": "raw close", "overlap_tolerance": 0.01,
            "points": {day: value for day, value in zip(series["dates"], series["values"]) if day >= "2026-09-29"},
        }

    def fetch(self, url, **kwargs):
        if url.startswith(updater.STOCK_ENDPOINT):
            return copy.deepcopy(self.stock_payload)
        if url == updater.WIND_URL:
            return copy.deepcopy(self.wind_payload)
        raise AssertionError("Unexpected request: " + url)

    def run_update(self, *, offline=False, now=NOW):
        self.output.write_text(json.dumps(self.before), encoding="utf-8")
        wind_mock = {"side_effect": URLError("Wind backup offline")} if offline else {"return_value": self.funddb}
        with mock.patch.object(updater, "fetch_json", side_effect=self.fetch), mock.patch.object(updater, "fetch_csi_backup", side_effect=URLError("stock backup offline")), mock.patch.object(updater, "fetch_wind_backup", **wind_mock):
            return updater.update_live(self.output, generated_at=now, allow_cached=True)

    def test_exact_three_date_migration_is_audited_and_idempotent(self):
        original = copy.deepcopy(self.before)
        result, changed = updater.reviewed_previous(self.before, NOW)
        self.assertTrue(changed)
        self.assertEqual(self.before, original)
        expected = {item["date"]: item for item in CHANGES}
        for old, row in zip(original["data"], result["data"]):
            if row[0] in expected:
                self.assertEqual(row[2], expected[row[0]]["new"])
                self.assertEqual(row[4], round((row[2] / updater.WIND_BASE - 1) * 100, 6))
                self.assertEqual((row[0], row[1], row[3]), (old[0], old[1], old[3]))
            else:
                self.assertEqual(row, old)
        self.assertEqual(result["as_of"], original["as_of"])
        entry = result["data_revisions"][0]
        self.assertEqual(entry["changes"], CHANGES)
        self.assertEqual(entry["index_code"], "885003.WI")
        self.assertEqual(entry["reviewed_at"], "2026-11-24")
        self.assertEqual(entry["applied_at"], "2026-11-24T04:00:00Z")
        self.assertEqual(len(entry["sources"]), 3)
        self.assertTrue(all(item["url"].startswith("https://") for item in entry["sources"]))
        again, changed = updater.reviewed_previous(result, datetime(2026, 11, 25, tzinfo=timezone.utc))
        self.assertFalse(changed)
        self.assertEqual(again, result)

    def test_partial_older_snapshot_records_only_existing_corrected_dates(self):
        self.before["data"].pop()
        self.before["as_of"] = "2026-11-19"
        for source in self.before["sources"].values():
            source["as_of"] = self.before["as_of"]
        result, changed = updater.reviewed_previous(self.before, NOW)
        self.assertTrue(changed)
        self.assertEqual(result["data_revisions"][0]["changes"], CHANGES[:2])
        self.assertEqual(result["as_of"], "2026-11-19")
        self.assertNotIn("2026-11-20", {row[0] for row in result["data"]})

    def test_unexpected_third_value_never_gets_overwritten(self):
        for unexpected in (4976.57, 4963.25, 4900.0):
            with self.subTest(unexpected=unexpected):
                previous = copy.deepcopy(self.before)
                row = next(row for row in previous["data"] if row[0] == "2026-11-19")
                row[2] = unexpected
                row[4] = round((unexpected / updater.WIND_BASE - 1) * 100, 6)
                original = copy.deepcopy(previous)
                with self.assertRaisesRegex(updater.DataValidationError, "unexpected published value"):
                    updater.reviewed_previous(previous, NOW)
                self.assertEqual(previous, original)

    def test_live_normal_overlap_confirms_corrections_then_appends_fetched_date(self):
        result = self.run_update()
        self.assertEqual(result["as_of"], "2026-11-23")
        self.assertEqual(result["data"][-1][2], 4950.07)
        self.assertEqual(result["data_revisions"][0]["changes"], CHANGES)
        source = result["sources"]["885003.WI"]
        self.assertEqual(source["mode"], "fallback")
        self.assertEqual(source["overlap_check"]["count"], 34)
        self.assertEqual(source["overlap_check"]["max_absolute_difference"], 0)
        self.assertEqual(source["overlap_check"]["tolerance"], 0.01)
        self.assertEqual(source["checks"][0]["status"], "rejected")
        self.assertIn("global source declares an H20269 fallback", source["checks"][0]["message"])
        self.assertEqual(result["update_status"]["outcome"], "fresh")
        self.before = copy.deepcopy(result)
        again = self.run_update(now=datetime(2026, 11, 25, tzinfo=timezone.utc))
        self.assertEqual(again["data_revisions"], result["data_revisions"])
        self.assertEqual(again["data"], result["data"])
        self.assertEqual(again["update_status"]["outcome"], "unchanged")
        cached = self.run_update(offline=True)
        self.assertEqual(cached["data_revisions"], result["data_revisions"])
        self.assertEqual(cached["data"], result["data"])

    def test_unrelated_source_change_is_still_rejected_without_revision(self):
        self.funddb["points"]["2026-11-17"] += 0.02
        result = self.run_update()
        self.assertEqual(result["data"], self.before["data"])
        self.assertNotIn("data_revisions", result)
        self.assertEqual(result["sources"]["885003.WI"]["mode"], "cached")
        self.assertIn("disagrees with verified history on 2026-11-17", result["sources"]["885003.WI"]["checks"][1]["message"])

    def test_cached_wind_cannot_publish_the_pending_migration(self):
        result = self.run_update(offline=True)
        self.assertEqual(result["data"], self.before["data"])
        self.assertEqual(result["generated_at"], self.before["generated_at"])
        self.assertNotIn("data_revisions", result)
        self.assertEqual(result["update_status"]["outcome"], "cached")

    def test_live_wind_with_cached_stock_discloses_applied_revisions(self):
        fetch = self.fetch
        def stock_offline(url, **kwargs):
            if url.startswith(updater.STOCK_ENDPOINT):
                raise URLError("stock primary offline")
            return fetch(url, **kwargs)
        with mock.patch.object(self, "fetch", side_effect=stock_offline):
            result = self.run_update()
        self.assertEqual(result["as_of"], self.before["as_of"])
        self.assertEqual(result["data_revisions"][0]["changes"], CHANGES)
        self.assertEqual(result["update_status"]["outcome"], "cached")
        self.assertIn("应用已披露的审阅修订", result["update_status"]["message"])
        self.assertTrue(any("除已披露的审阅修订外" in message for message in result["update_status"]["warnings"]))

    def test_failed_new_date_alignment_cannot_leak_migration_into_cached_choice(self):
        self.stock_payload["data"].append({"indexCode": "H00300", "tradeDate": "20261124", "close": 6030.0})
        self.funddb["points"].pop("2026-11-23")
        self.funddb["points"]["2026-11-24"] = 4960.0
        result = self.run_update(now=datetime(2026, 11, 25, tzinfo=timezone.utc))
        self.assertEqual(result["sources"]["885003.WI"]["checks"][1]["status"], "available")
        self.assertEqual(result["sources"]["885003.WI"]["mode"], "cached")
        self.assertEqual(result["data"], self.before["data"])
        self.assertNotIn("data_revisions", result)
        self.assertTrue(any("共同日期" in message for message in result["update_status"]["warnings"]))

    def test_migration_never_inserts_the_expected_new_close(self):
        self.funddb["points"].pop("2026-11-23")
        result = self.run_update()
        self.assertEqual(result["as_of"], "2026-11-20")
        self.assertEqual(len(result["data"]), len(self.before["data"]))
        self.assertNotIn("2026-11-23", {row[0] for row in result["data"]})
        self.assertEqual(result["data_revisions"][0]["changes"], CHANGES)


if __name__ == "__main__":
    unittest.main()

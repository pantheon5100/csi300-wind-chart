"""Outages must not move dates backward, rewrite history or create crossings."""

import copy
from datetime import date, datetime, timedelta, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("failover_updater", ROOT / "scripts" / "update_data.py")
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)
NOW = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)


class FailoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = ROOT / "tests" / "fixtures"
        cls.stock = json.loads(gzip.decompress((folder / "stock.json.gz").read_bytes()))
        cls.wind = json.loads(gzip.decompress((folder / "wind.json.gz").read_bytes()))
        # The archive stays unchanged. These failover tests use deliberately
        # synthetic trusted provenance, not the real ambiguous global note.
        cls.wind["source"] = "Synthetic trusted Wind 885003.WI metadata for unit tests only"
        cls.snapshot = updater.build_dataset(cls.stock, cls.wind, generated_at=NOW)

    def setUp(self):
        # Reviewed historical corrections have their own integration tests;
        # these cases retain their original no-history-change contract.
        revisions = mock.patch.object(updater, "REVIEWED_REVISIONS", ())
        revisions.start()
        self.addCleanup(revisions.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "data.json"
        self.before = copy.deepcopy(self.snapshot)
        self.stock_payload = copy.deepcopy(self.stock)
        self.wind_payload = copy.deepcopy(self.wind)
        self.stock_error = self.wind_error = False
        self.csi = self.candidate("H00300")
        self.funddb = self.candidate("885003.WI")

    def candidate(self, code):
        provider = updater.PROVIDERS[code][1]
        return {
            "code": code, "name": provider["name"], "provider_id": provider["provider_id"],
            "url": provider["url"], "points": updater.previous_points(self.snapshot, code),
            "precision": "raw close", "overlap_tolerance": 0.01,
        }

    def trim_previous(self):
        self.before["data"].pop()
        self.before["as_of"] = self.before["data"][-1][0]
        for source in self.before["sources"].values():
            source["as_of"] = self.before["as_of"]

    def fetch(self, url, **kwargs):
        if url.startswith(updater.STOCK_ENDPOINT):
            if self.stock_error: raise URLError("primary stock offline")
            return self.stock_payload
        if url == updater.WIND_URL:
            if self.wind_error: raise URLError("primary wind offline")
            return self.wind_payload
        raise AssertionError("Unexpected request: " + url)

    def run_update(self, *, allow_cached=True, now=NOW):
        self.output.write_text(json.dumps(self.before), encoding="utf-8")
        with mock.patch.object(updater, "fetch_json", side_effect=self.fetch), mock.patch.object(updater, "fetch_csi_backup", return_value=self.csi), mock.patch.object(updater, "fetch_wind_backup", return_value=self.funddb):
            return updater.update_live(self.output, generated_at=now, allow_cached=allow_cached)

    def test_equal_date_prefers_primary_and_checks_both_providers(self):
        result = self.run_update()
        self.assertEqual(result["data"], self.before["data"])
        self.assertEqual(result["update_status"]["outcome"], "unchanged")
        for source in result["sources"].values():
            self.assertEqual(source["mode"], "primary")
            self.assertEqual([check["status"] for check in source["checks"]], ["available", "available"])

    def test_backups_append_after_outage_without_replacing_old_history(self):
        self.trim_previous()
        self.stock_error = self.wind_error = True
        # Known historical differences outside the recent guard are never imported.
        self.csi["points"]["2006-04-21"] += 4
        self.funddb["points"]["2006-04-21"] += 4
        result = self.run_update()
        self.assertEqual(result["data"][:-1], self.before["data"])
        self.assertEqual(result["data"][-1], self.snapshot["data"][-1])
        self.assertEqual(result["as_of"], "2026-11-19")
        self.assertEqual(result["update_status"]["outcome"], "fresh")
        for source in result["sources"].values():
            self.assertEqual(source["mode"], "fallback")
            self.assertEqual(len(source["history"]), 2)
            self.assertEqual(source["history"][-1]["from"], "2026-11-19")
            self.assertEqual(source["history"][-1]["to"], "2026-11-19")

    def test_stale_backups_cannot_regress_the_published_date(self):
        self.stock_error = self.wind_error = True
        for candidate in (self.csi, self.funddb):
            candidate["points"].pop("2026-11-19")
        result = self.run_update()
        self.assertEqual(result["data"], self.before["data"])
        self.assertEqual(result["as_of"], self.before["as_of"])
        self.assertEqual(result["generated_at"], self.before["generated_at"])
        self.assertEqual(result["update_status"]["outcome"], "cached")
        for source in result["sources"].values():
            self.assertEqual(source["mode"], "cached")
            self.assertEqual(source["checks"][-1]["status"], "older")

    def test_wrong_index_and_misaligned_recent_values_are_rejected(self):
        self.stock_error = self.wind_error = True
        self.csi["code"] = "000300"
        self.funddb["points"]["2026-11-13"] += 5
        result = self.run_update()
        self.assertEqual(result["data"], self.before["data"])
        for source in result["sources"].values():
            self.assertEqual(source["checks"][-1]["status"], "rejected")

    def test_recent_overlap_requires_coverage_and_at_least_twenty_dates(self):
        self.stock_error = self.wind_error = True
        self.csi["points"] = dict(list(self.csi["points"].items())[-19:])
        self.funddb["points"].pop("2026-11-12")
        result = self.run_update()
        self.assertEqual(result["update_status"]["outcome"], "cached")
        self.assertEqual(result["sources"]["H00300"]["checks"][-1]["status"], "rejected")
        self.assertEqual(result["sources"]["885003.WI"]["checks"][-1]["status"], "rejected")

    def test_old_gap_can_end_overlap_only_after_twenty_consecutive_dates(self):
        self.stock_error = self.wind_error = True
        days = list(self.funddb["points"])
        self.funddb["points"].pop(days[-30])
        result = self.run_update()
        source = result["sources"]["885003.WI"]
        self.assertEqual(source["mode"], "fallback")
        self.assertEqual(source["overlap_check"]["count"], 29)
        self.assertEqual(result["data"], self.before["data"])

    def test_unused_old_weekend_is_ignored_but_new_weekend_is_rejected(self):
        self.stock_error = self.wind_error = True
        self.funddb["points"]["2026-05-31"] = 4900.0
        self.funddb["points"] = dict(sorted(self.funddb["points"].items()))
        result = self.run_update()
        self.assertEqual(result["sources"]["885003.WI"]["mode"], "fallback")
        self.assertEqual(result["data"], self.before["data"])
        self.funddb["points"]["2026-11-21"] = 4977.0
        result = self.run_update(now=datetime(2026, 11, 23, tzinfo=timezone.utc))
        self.assertEqual(result["sources"]["885003.WI"]["mode"], "cached")
        self.assertEqual(result["sources"]["885003.WI"]["checks"][-1]["status"], "rejected")

    def test_fresher_backup_can_beat_a_lagging_but_working_primary(self):
        self.trim_previous()
        self.stock_payload["data"].pop()
        series = self.wind_payload["series"]["885003.WI"]
        series["dates"].pop()
        series["values"].pop()
        result = self.run_update()
        self.assertEqual(result["as_of"], "2026-11-19")
        self.assertTrue(all(source["mode"] == "fallback" for source in result["sources"].values()))

    def test_only_one_available_index_cannot_create_unpaired_new_data(self):
        self.trim_previous()
        self.wind_error = True
        self.funddb["code"] = "wrong"
        result = self.run_update()
        self.assertEqual(result["data"], self.before["data"])
        self.assertEqual(result["update_status"]["outcome"], "cached")
        self.assertEqual(result["sources"]["H00300"]["as_of"], "2026-11-19")
        self.assertEqual(result["sources"]["885003.WI"]["as_of"], "2026-11-18")

    def test_primary_history_changes_are_rejected_without_rewriting_the_page(self):
        self.wind_payload["series"]["885003.WI"]["values"][300] += 100
        self.funddb["code"] = "wrong"
        result = self.run_update()
        self.assertEqual(result["data"], self.before["data"])
        self.assertEqual(result["sources"]["885003.WI"]["checks"][0]["status"], "rejected")

    def test_strict_mode_fails_without_replacing_existing_file(self):
        self.stock_error = True
        self.csi["code"] = "wrong"
        with self.assertRaises(updater.DataValidationError):
            self.run_update(allow_cached=False)
        self.assertEqual(json.loads(self.output.read_text()), self.before)

    def test_new_date_alignment_failure_keeps_verified_snapshot(self):
        self.stock_error = self.wind_error = True
        day = date(2026, 11, 20)
        new_days = []
        while len(new_days) < 12:
            if day.weekday() < 5: new_days.append(day.isoformat())
            day += timedelta(days=1)
        self.csi["points"].update({day: 6002.0 for day in new_days})
        self.funddb["points"].update({day: 4977.0 for day in new_days[1::2]})
        result = self.run_update(now=datetime(2026, 12, 8, tzinfo=timezone.utc))
        self.assertEqual(result["data"], self.before["data"])
        self.assertEqual(result["update_status"]["outcome"], "cached")
        self.assertTrue(any("共同日期" in warning for warning in result["update_status"]["warnings"]))

    def test_even_one_missing_new_interior_date_is_not_skipped_forever(self):
        self.stock_error = self.wind_error = True
        self.csi["points"].update({"2026-11-20": 6002.0, "2026-11-23": 6003.0})
        self.funddb["points"].update({"2026-11-23": 4977.0})
        result = self.run_update(now=datetime(2026, 11, 24, tzinfo=timezone.utc))
        self.assertEqual(result["data"], self.before["data"])
        self.assertEqual(result["as_of"], "2026-11-19")
        self.assertEqual(result["update_status"]["outcome"], "cached")

    def test_bad_fresher_backup_pair_does_not_block_good_primary_pair(self):
        self.trim_previous()
        self.csi["points"].update({"2026-11-20": 6002.0, "2026-11-23": 6003.0})
        self.funddb["points"].update({"2026-11-23": 4977.0})
        result = self.run_update(now=datetime(2026, 11, 24, tzinfo=timezone.utc))
        self.assertEqual(result["as_of"], "2026-11-19")
        self.assertEqual(result["update_status"]["outcome"], "fresh")
        self.assertTrue(all(source["mode"] == "primary" for source in result["sources"].values()))

    def test_future_dated_previous_snapshot_is_rejected_before_source_reads(self):
        self.before["data"][-1][0] = self.before["as_of"] = "2027-11-19"
        for source in self.before["sources"].values(): source["as_of"] = "2027-11-19"
        with self.assertRaisesRegex(updater.DataValidationError, "future-dated"):
            self.run_update()


if __name__ == "__main__":
    unittest.main()

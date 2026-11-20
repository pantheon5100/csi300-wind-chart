"""Verify exact series identity and safe extraction from the public HTML shape."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("backup_sources", ROOT / "scripts" / "backup_sources.py")
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)
FIXTURE = ROOT / "tests" / "fixtures" / "csi_backup.html"


def page(payload):
    return ("<script>const DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n</script>").encode()


class CsiBackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = FIXTURE.read_bytes()
        cls.payload = backup._embedded_data(cls.raw)

    def test_archived_public_observations_and_contract(self):
        result = backup.parse_csi_backup(self.raw)
        self.assertEqual(result["code"], "H00300")
        self.assertEqual(result["provider_id"], "charlespai_tushare")
        self.assertEqual(result["overlap_tolerance"], 0.01)
        self.assertEqual(result["as_of"], "2026-11-18")
        self.assertEqual(result["points"]["2004-12-31"], 1000.0)
        self.assertEqual(result["points"]["2026-11-18"], 5999.43)
        # Keep the observed Tushare value; do not rewrite it to match CSI history.
        self.assertEqual(result["points"]["2006-04-21"], 1150.07)

    def test_fetch_uses_only_the_documented_public_url(self):
        transport = mock.Mock(return_value=self.raw)
        self.assertEqual(backup.fetch_csi_backup(transport)["as_of"], "2026-11-18")
        transport.assert_called_once_with("https://research.charlespai.com/cn/")

    def test_price_index_and_net_return_substitutes_are_rejected(self):
        for key, name in [("000300", "沪深300"), ("n00300", "沪深300净收益"), ("h00300", "沪深300")]:
            with self.subTest(key=key, name=name):
                payload = copy.deepcopy(self.payload)
                series = payload["tr"].pop("h00300")
                series["name"] = name
                payload["tr"][key] = series
                with self.assertRaises(backup.CsiBackupError):
                    backup.parse_csi_backup(page(payload))

    def test_duplicate_unordered_or_invalid_dates_fail(self):
        for value in ("2004-12-31", "2004-12-30", "2005-1-4", "2005-02-30", None):
            with self.subTest(value=value):
                payload = copy.deepcopy(self.payload)
                payload["tr"]["h00300"]["dates"][1] = value
                with self.assertRaises(backup.CsiBackupError):
                    backup.parse_csi_backup(page(payload))

    def test_bad_levels_and_different_array_lengths_fail(self):
        for value in (None, True, "5999.43", 0, -5, float("nan"), float("inf")):
            with self.subTest(value=value):
                payload = copy.deepcopy(self.payload)
                payload["tr"]["h00300"]["close"][1] = value
                with self.assertRaises(backup.CsiBackupError):
                    backup.parse_csi_backup(page(payload))
        payload = copy.deepcopy(self.payload)
        payload["tr"]["h00300"]["close"].pop()
        with self.assertRaises(backup.CsiBackupError):
            backup.parse_csi_backup(page(payload))

    def test_metadata_and_baseline_must_match_observations(self):
        for field, value in [("as_of", "2026-11-19"), ("since", "2005-01-04"), ("baseline", 999.0)]:
            with self.subTest(field=field):
                payload = copy.deepcopy(self.payload)
                if field == "baseline":
                    payload["tr"]["h00300"]["close"][0] = value
                else:
                    payload["tr"]["h00300"][field] = value
                with self.assertRaises(backup.CsiBackupError):
                    backup.parse_csi_backup(page(payload))

    def test_only_one_closed_inline_literal_assignment_is_accepted(self):
        for raw in (
            b'<p>const DATA = {};</p>',
            b'<script src="external.js">const DATA = {};</script>',
            b'<script>const DATA = {}',
            b'<script>const DATA = JSON.parse("{}");</script>',
            b'<script>const DATA = {} + runSomething();</script>',
            b'<script>const DATA = {"tr":{},"tr":{}};</script>',
            self.raw + self.raw,
        ):
            with self.subTest(raw=raw[:70]):
                with self.assertRaises(backup.CsiBackupError):
                    backup.parse_csi_backup(raw)

    def test_extraction_size_limits_and_utf8_are_enforced(self):
        with mock.patch.object(backup, "MAX_HTML_BYTES", 20):
            with self.assertRaises(backup.CsiBackupError):
                backup.parse_csi_backup(self.raw)
        with mock.patch.object(backup, "MAX_JSON_CHARS", 20):
            with self.assertRaises(backup.CsiBackupError):
                backup.parse_csi_backup(self.raw)
        with self.assertRaises(backup.CsiBackupError):
            backup.parse_csi_backup(b"\xff\xfe")


if __name__ == "__main__":
    unittest.main()

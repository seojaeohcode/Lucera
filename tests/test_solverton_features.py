from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lucera.db import LuceraDB
from lucera.solverton_features import yeongam_f1, yeongam_f3, yeongam_f4
from lucera.synthetic import seed_synthetic


class SolvertonFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = LuceraDB(Path(self.temp.name) / "features.sqlite3")
        self.db.initialize(Path(__file__).parents[1] / "db" / "schema.sql")
        seed_synthetic(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def test_f1_is_yeongam_only_and_has_source_article(self) -> None:
        result = yeongam_f1()
        self.assertEqual(result["scope"], "영암군")
        self.assertEqual(result["rule"]["county"], "영암군")
        self.assertEqual(result["rule"]["article"], "제20조의3")
        self.assertEqual(result["rule"]["road_m"], 300.0)

    def test_f3_contains_only_yeongam_ri_rows(self) -> None:
        result = yeongam_f3(self.db)
        self.assertTrue(result["items"])
        self.assertTrue(all(row["eup_myeon"] for row in result["items"]))
        self.assertTrue(all("영암" not in row["ri"] for row in result["items"]))
        self.assertEqual(result["items"][0]["rank"], 1)

    def test_f4_contains_only_yeongam_eup_myeon_rows(self) -> None:
        result = yeongam_f4(self.db)
        self.assertTrue(result["items"])
        self.assertEqual(len(result["items"]), 11)
        self.assertTrue(all(row["eup_myeon"] for row in result["items"]))
        self.assertFalse(any("함평" in row["eup_myeon"] for row in result["items"]))
        self.assertTrue(all(row["supply_data_present"] for row in result["items"]))


if __name__ == "__main__":
    unittest.main()

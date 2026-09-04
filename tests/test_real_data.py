from __future__ import annotations

import unittest
from collections import Counter
from pathlib import Path

from lucera.real_data import load_yeongam_permits, select_coordinate_sample


class RealYeongamDataTests(unittest.TestCase):
    def test_official_register_is_yeongam_only(self) -> None:
        path = Path(__file__).parents[1] / "data" / "reference" / "yeongam_solar_permits_20260301.csv"
        permits = load_yeongam_permits(path)
        self.assertEqual(len(permits), 1549)
        self.assertTrue(all(item["city_county"] == "영암군" for item in permits))

    def test_map_sample_is_balanced_by_ri_and_never_duplicates_parcel(self) -> None:
        path = Path(__file__).parents[1] / "data" / "reference" / "yeongam_solar_permits_20260301.csv"
        sample = select_coordinate_sample(load_yeongam_permits(path), per_ri=4)
        group_counts = Counter((item["eup_myeon"], item["ri"]) for item in sample)
        self.assertEqual(len(sample), len({item["source_record_key"] for item in sample}))
        self.assertTrue(all(count <= 4 for count in group_counts.values()))
        self.assertGreaterEqual(len(group_counts), 100)


if __name__ == "__main__":
    unittest.main()

"""Synthetic metadata unit fixtures; not demo satellite products or predictions.

Run: python -m unittest discover -s tests -p test_s1_discovery.py -v
Geometry integration tests require the real discovery extra and skip explicitly
when those dependencies are unavailable. No network requests are made in tests.
"""

import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from trinetra.discovery import sentinel1 as s1

EVENT = datetime(2026, 8, 26, tzinfo=timezone.utc)
GEOMETRY = {"type": "Polygon", "coordinates": [[[85.1, 27.9], [85.2, 27.9], [85.2, 28.0], [85.1, 28.0], [85.1, 27.9]]]}


def scene(name, days, *, orbit=85, direction="ascending", platform="sentinel-1a", coverage=1):
    start = EVENT + timedelta(days=days)
    return s1.Scene(name, start, start + timedelta(seconds=25), orbit, direction, platform, coverage, GEOMETRY)


def item(name="TEST-METADATA-ONLY", days=-2):
    return {"type": "Feature", "id": name, "collection": s1.COLLECTION, "geometry": GEOMETRY,
            "properties": {"product:type": "IW_GRDH_1S", "sar:instrument_mode": "IW",
                           "sar:polarizations": ["VV", "VH"], "sat:relative_orbit": 85,
                           "sat:orbit_state": "ascending", "platform": "sentinel-1a",
                           "start_datetime": s1._iso(EVENT + timedelta(days=days)),
                           "end_datetime": s1._iso(EVENT + timedelta(days=days, seconds=25))}}


class DateAndPairTests(unittest.TestCase):
    def choose(self, scenes, common=lambda a, b: 1):
        return s1._choose_pair(scenes, EVENT, EVENT + timedelta(days=1), "whole_utc_day_excluded", 30, .99, common)

    def test_date_only_excludes_whole_day(self):
        start, end, mode = s1._event_window("2026-08-26")
        self.assertEqual(start, EVENT)
        self.assertEqual(end - start, timedelta(days=1))
        self.assertEqual(mode, "whole_utc_day_excluded")

    def test_nepal_timestamp_converted_to_utc(self):
        start, end, mode = s1._event_window("2026-08-26T06:00:00+05:45")
        self.assertEqual(s1._iso(start), "2026-08-26T00:15:00Z")
        self.assertEqual(start, end)
        self.assertEqual(mode, "instant")

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            s1._event_window("2026-08-26T06:00:00")

    def test_closest_valid_observations_chosen(self):
        pre, post, _ = self.choose([scene("pre-old", -20), scene("pre-new", -2),
                                    scene("post-late", 8), scene("post-early", 2)])
        self.assertEqual((pre.product_id, post.product_id), ("pre-new", "post-early"))

    def test_wrong_orbit_rejected(self):
        with self.assertRaises(s1.NoSuitablePair):
            self.choose([scene("pre", -2), scene("post", 2, orbit=86)])

    def test_wrong_direction_rejected(self):
        with self.assertRaises(s1.NoSuitablePair):
            self.choose([scene("pre", -2), scene("post", 2, direction="descending")])

    def test_partial_footprint_rejected(self):
        with self.assertRaises(s1.NoSuitablePair):
            self.choose([scene("pre", -2, coverage=.5), scene("post", 2)])

    def test_common_coverage_enforced(self):
        with self.assertRaises(s1.NoSuitablePair):
            self.choose([scene("pre", -2), scene("post", 2)], lambda a, b: .98)

    def test_event_day_scene_not_post(self):
        with self.assertRaises(s1.NoSuitablePair):
            self.choose([scene("pre", -2), scene("event-day", .5)])

    def test_straddling_pre_rejected(self):
        start = EVENT - timedelta(seconds=10)
        crossing = s1.Scene("crossing", start, EVENT + timedelta(seconds=10), 85, "ascending", "sentinel-1a", 1, GEOMETRY)
        with self.assertRaises(s1.NoSuitablePair):
            self.choose([crossing, scene("post", 2)])

    def test_search_window_is_bounded(self):
        with self.assertRaises(s1.NoSuitablePair):
            self.choose([scene("too-old", -31), scene("post", 2)])

    def test_same_platform_preferred(self):
        pre, post, _ = self.choose([scene("pre", -3), scene("cross-platform", 2, platform="sentinel-1c"), scene("same-platform", 4)])
        self.assertEqual(post.product_id, "same-platform")

    def test_cross_platform_allowed_when_geometry_matches(self):
        pre, post, _ = self.choose([scene("pre", -3), scene("post", 2, platform="sentinel-1c")])
        self.assertNotEqual(pre.platform, post.platform)

    def test_no_scenes_not_false_success(self):
        with self.assertRaises(s1.NoSuitablePair):
            self.choose([])

    def test_exact_event_instant_scene_not_post(self):
        with self.assertRaises(s1.NoSuitablePair):
            s1._choose_pair([scene("pre", -1), scene("event", 0)], EVENT, EVENT, "instant", 30, .99, lambda a, b: 1)


class MetadataTests(unittest.TestCase):
    def test_required_metadata_parsed_without_ids_in_code(self):
        parsed = s1._scene(item(), lambda geometry: 1)
        self.assertEqual(parsed.relative_orbit, 85)
        self.assertEqual(parsed.platform, "sentinel-1a")

    def test_missing_orbit_not_inferred(self):
        value = item()
        del value["properties"]["sat:relative_orbit"]
        with self.assertRaises(ValueError):
            s1._scene(value, lambda geometry: 1)

    def test_missing_acquisition_end_rejected(self):
        value = item()
        del value["properties"]["end_datetime"]
        with self.assertRaises(ValueError):
            s1._scene(value, lambda geometry: 1)

    def test_unsupported_products_rejected(self):
        for key, invalid in [("product:type", "SLC"), ("sar:instrument_mode", "EW"),
                             ("sar:polarizations", ["HH", "HV"]), ("sat:orbit_state", None),
                             ("sat:relative_orbit", True), ("platform", None)]:
            with self.subTest(key=key):
                value = item()
                value["properties"][key] = invalid
                with self.assertRaises(ValueError):
                    s1._scene(value, lambda geometry: 1)

    def test_fresh_cache_no_second_request_and_tuple_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            query = {"coordinates": ((1, 2), (3, 4))}
            fetch = Mock(return_value=[item()])
            first = s1._cached_metadata(query, folder, 24, False, fetch)
            second = s1._cached_metadata(query, folder, 24, False, fetch)
            self.assertFalse(first[2])
            self.assertTrue(second[2])
            self.assertEqual(fetch.call_count, 1)

    def test_corrupt_cache_refetched(self):
        with tempfile.TemporaryDirectory() as folder:
            fetch = Mock(return_value=[item()])
            s1._cached_metadata({}, folder, 24, False, fetch)
            next(Path(folder).glob("*.json")).write_text("broken", encoding="utf-8")
            self.assertFalse(s1._cached_metadata({}, folder, 24, False, fetch)[2])
            self.assertEqual(fetch.call_count, 2)

    def test_expired_cache_not_used_when_network_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            s1._cached_metadata({}, folder, 24, False, lambda query: [])
            path = next(Path(folder).glob("*.json"))
            value = json.loads(path.read_text())
            value["fetched_at"] = "2020-01-01T00:00:00Z"
            path.write_text(json.dumps(value))
            with self.assertRaises(s1.CatalogError):
                s1._cached_metadata({}, folder, 24, False, Mock(side_effect=s1.CatalogError("network blocked")))

    def test_refresh_and_different_query_refetch(self):
        with tempfile.TemporaryDirectory() as folder:
            fetch = Mock(return_value=[])
            s1._cached_metadata({"date": 1}, folder, 24, False, fetch)
            s1._cached_metadata({"date": 1}, folder, 24, True, fetch)
            s1._cached_metadata({"date": 2}, folder, 24, False, fetch)
            self.assertEqual(fetch.call_count, 3)

    def test_pagination_with_merged_post_body(self):
        first = {"type": "FeatureCollection", "features": [item("first")], "links": [
            {"rel": "next", "href": s1.CATALOG + "/search", "method": "POST", "merge": True, "body": {"token": "page-2"}}]}
        second = {"type": "FeatureCollection", "features": [item("second")], "links": []}
        with patch.object(s1, "_request_json", side_effect=[first, second]) as request:
            result = s1._fetch_metadata({"limit": 100})
        self.assertEqual(len(result), 2)
        self.assertEqual(request.call_args_list[1].args[2], {"limit": 100, "token": "page-2"})
        self.assertNotIn("assets", result[0])

    def test_foreign_pagination_url_rejected(self):
        page = {"type": "FeatureCollection", "features": [], "links": [
            {"rel": "next", "href": "https://example.com/next"}]}
        with patch.object(s1, "_request_json", return_value=page), self.assertRaises(s1.CatalogError):
            s1._fetch_metadata({})

    def test_page_limit_fails_instead_of_partial_success(self):
        page = {"type": "FeatureCollection", "features": [item()], "links": [
            {"rel": "next", "href": s1.CATALOG + "/search?page=2"}]}
        with patch.object(s1, "_request_json", return_value=page), self.assertRaises(s1.CatalogError):
            s1._fetch_metadata({}, max_pages=1)

    def test_invalid_catalog_response_not_empty_success(self):
        with patch.object(s1, "_request_json", return_value={"error": "unavailable"}), self.assertRaises(s1.CatalogError):
            s1._fetch_metadata({})

    def test_public_function_uses_aoi_date_not_manual_ids(self):
        # Isolate orchestration with explicitly mocked geometry and catalog.
        engine = Mock(geojson=GEOMETRY, area=1000000)
        engine.coverage.return_value = 1
        engine.common.return_value = 1
        with tempfile.TemporaryDirectory() as folder, patch.object(s1, "_Coverage", return_value=engine), patch.object(s1, "_fetch_metadata", return_value=[item("TEST-pre", -2), item("TEST-post", 2)]) as fetch:
            first = s1.select_s1_pair(GEOMETRY, "2026-08-26", cache_dir=folder)
            second = s1.select_s1_pair(GEOMETRY, "2026-08-26", cache_dir=folder)
            self.assertEqual((first.pre_id, first.post_id), ("TEST-pre", "TEST-post"))
            self.assertEqual(first.delta_days, 4)
            self.assertTrue(second.cache_hit)
            self.assertEqual(fetch.call_count, 1)
            query = fetch.call_args.args[0]
            self.assertEqual(query["intersects"], GEOMETRY)
            self.assertNotIn("ids", query)


HAS_GEO = importlib.util.find_spec("shapely") is not None and importlib.util.find_spec("pyproj") is not None


@unittest.skipUnless(HAS_GEO, "Real geometry checks require installed shapely and pyproj")
class GeometryTests(unittest.TestCase):
    def test_actual_full_partial_and_common_coverage(self):
        engine = s1._Coverage(GEOMETRY)
        self.assertAlmostEqual(engine.coverage(GEOMETRY), 1, places=7)
        partial = copy.deepcopy(GEOMETRY)
        partial["coordinates"][0][1][0] = 85.15
        partial["coordinates"][0][2][0] = 85.15
        self.assertAlmostEqual(engine.coverage(partial), .5, places=3)
        pre = scene("pre", -2)
        post = s1.Scene("post", EVENT + timedelta(days=2), EVENT + timedelta(days=2, seconds=25), 85, "ascending", "sentinel-1a", .5, partial)
        self.assertAlmostEqual(engine.common(pre, post), .5, places=3)

    def test_invalid_aoi_and_broad_search_rejected(self):
        for aoi in [[-180, -80, 180, 80], [85, 28, 85, 28], {"type": "Point", "coordinates": [85, 28]},
                    {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]}]:
            with self.subTest(aoi=aoi), self.assertRaises(ValueError):
                s1._Coverage(aoi)


if __name__ == "__main__":
    unittest.main()

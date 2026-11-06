import importlib.util
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SCRIPT = Path(__file__).resolve().parents[1] / "skills/map/scripts/reel_map.py"
spec = importlib.util.spec_from_file_location("reel_map", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MapsTests(unittest.TestCase):
    def test_rejects_non_instagram_and_credentials(self):
        for url in ["https://instagram.com.evil.test/reel/abc/", "http://instagram.com/reel/abc/", "https://user@instagram.com/reel/abc/", "https://instagram.com/"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                module.instagram_url(url)
        self.assertEqual(module.instagram_url("https://www.instagram.com/reel/abc?utm_source=x"), "https://www.instagram.com/reel/abc/")

    def test_no_places_is_not_an_invented_map(self):
        result = module.build_links({"places": []})
        self.assertEqual(result["routes"], [])
        self.assertEqual(result["shortlists"], [])

    def test_special_characters_preserved_in_destination(self):
        result = module.build_links({"places": [{"name": "Café & Co / 市場", "address": "London, UK"}]})
        query = parse_qs(urlsplit(result["routes"][0]["url"]).query)
        self.assertEqual(query["query"], ["Café & Co / 市場, London, UK"])

    def test_large_routes_never_drop_a_place(self):
        names = ["Place " + str(i) for i in range(27)]
        result = module.build_links({"places": [{"name": n} for n in names]})
        for field, maximum in [("routes", 10), ("mobile_browser_routes", 5)]:
            found = []
            for route in result[field]:
                self.assertLessEqual(len(route["places"]), maximum)
                self.assertLessEqual(len(route["url"]), 2048)
                found.extend(route["places"])
            self.assertEqual(set(found), set(names))
        self.assertFalse(result["shortlists"])

    def test_partial_place_ids_do_not_generate_incomplete_shortlist(self):
        result = module.build_links({"places": [
            {"name": "One", "google_place_id": "ChIJ1234567890123456", "place_id_source": "https://www.google.com/maps/"},
            {"name": "Two"},
        ]})
        self.assertEqual(result["unmatched_places"], ["Two"])
        self.assertEqual(result["shortlists"], [])
        self.assertEqual(len(result["routes"]), 1)

    def test_sourced_ids_generate_shortlists_of_at_most_twenty(self):
        places = [{"name": "Fixture " + str(i), "google_place_id": "TestPlaceId%08d" % i,
                   "place_id_source": "https://example.com/test-fixture"} for i in range(23)]
        result = module.build_links({"places": places})
        self.assertEqual([len(x["places"]) for x in result["shortlists"]], [20, 3])
        for item in result["shortlists"]:
            query = parse_qs(urlsplit(item["url"]).query)
            self.assertEqual(query["map_action"], ["shortlist"])

    def test_ids_need_a_source(self):
        with self.assertRaises(ValueError):
            module.build_links({"places": [{"name": "One", "google_place_id": "ChIJ1234567890123456"}]})

    def test_deduplication_preserves_distinct_branches(self):
        result = module.build_links({"places": [{"name": "Cafe", "address": "London"},
                                                {"name": "cafe", "address": "london"},
                                                {"name": "Cafe", "address": "Oxford"}]})
        self.assertEqual(result["place_count"], 2)

    def test_long_unicode_addresses_split_within_url_limit(self):
        result = module.build_links({"places": [{"name": str(i), "address": "界" * 80} for i in range(8)]})
        self.assertGreater(len(result["routes"]), 1)
        self.assertTrue(all(len(r["url"]) <= 2048 for r in result["routes"]))


if __name__ == "__main__":
    unittest.main()

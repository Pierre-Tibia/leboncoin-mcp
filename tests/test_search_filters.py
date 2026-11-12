"""Tests des nouveaux filtres de search_ads (search_filters.py et server.search_ads).

Lancer depuis la racine du projet :  python -m unittest discover tests

Leboncoin n'est jamais appelé : le client lbc est remplacé par un faux client,
et on vérifie ce que search_ads lui aurait envoyé.
"""

import unittest
from types import SimpleNamespace
from unittest import mock

import lbc

import server
from search_filters import (NO_MAXIMUM, build_real_estate_filters, city_matches,
                            keep_only_cities, location_matches)

ANTIBES = {"latitude": 43.58, "longitude": 7.12}


def fake_ad(ad_id: int, city_label: str) -> SimpleNamespace:
    location = SimpleNamespace(city_label=city_label, zipcode=city_label[-5:], city=city_label[:-6], department_name="Alpes-Maritimes",
                               region_name="Provence-Alpes-Côte d'Azur", lat=43.58, lng=7.12)
    return SimpleNamespace(
        id=ad_id, subject="Appartement", price=200000, url=f"https://www.leboncoin.fr/ad/{ad_id}",
        category_name="Ventes immobilières", ad_type="offer", body="", images=[],
        first_publication_date="2026-11-01 08:00:00", location=location, attributes={}, has_phone=False,
    )


# Réponse mélangée, comme lors de la dernière recherche : Antibes + communes voisines.
MIXED_CITIES = ["Antibes 06600", "Juan-les-Pins 06160", "Vallauris 06220", "Mougins 06250",
                "Cagnes-sur-Mer 06800", "Antibes 06160"]


def run_search(ads_cities=MIXED_CITIES, **params):
    """Lance search_ads avec un faux client. Renvoie (résultat, arguments envoyés à Leboncoin)."""
    params.setdefault("full", True)  # ces tests lisent la réponse complète
    with mock.patch.object(server, "_client") as client:
        client.search.return_value = SimpleNamespace(
            total=len(ads_cities), total_pro=0, total_private=len(ads_cities), max_pages=1,
            ads=[fake_ad(i, label) for i, label in enumerate(ads_cities)],
        )
        result = server.search_ads(**params)
        return result, client.search.call_args.kwargs


class RealEstateParametersTest(unittest.TestCase):
    """Un test par paramètre : on vérifie le filtre envoyé à Leboncoin."""

    def test_surface_min(self):
        _, sent = run_search(surface_min=40)
        self.assertEqual(sent["square"], [40, NO_MAXIMUM])

    def test_surface_max(self):
        _, sent = run_search(surface_max=80)
        self.assertEqual(sent["square"], [0, 80])

    def test_rooms_min(self):
        _, sent = run_search(rooms_min=2)
        self.assertEqual(sent["rooms"], [2, NO_MAXIMUM])

    def test_rooms_max(self):
        _, sent = run_search(rooms_max=4)
        self.assertEqual(sent["rooms"], [0, 4])

    def test_bedrooms_min(self):
        _, sent = run_search(bedrooms_min=1)
        self.assertEqual(sent["bedrooms"], [1, NO_MAXIMUM])

    def test_property_type_appartement(self):
        _, sent = run_search(property_type="appartement")
        self.assertEqual(sent["real_estate_type"], ["2"])

    def test_property_type_ignores_case(self):
        _, sent = run_search(property_type="Maison")
        self.assertEqual(sent["real_estate_type"], ["1"])

    def test_no_filter_sends_nothing_new(self):
        _, sent = run_search()
        for key in ("square", "rooms", "bedrooms", "real_estate_type"):
            self.assertNotIn(key, sent)

    def test_filters_are_accepted_by_lbc_library(self):
        # Le format envoyé doit être compris par lbc (sinon il lève InvalidValue).
        payload = lbc.utils.build_search_payload_with_args(**build_real_estate_filters(
            property_type="appartement", surface_min=40, rooms_min=2, bedrooms_min=1))
        self.assertEqual(payload["filters"]["ranges"]["square"], {"min": 40, "max": NO_MAXIMUM})
        self.assertEqual(payload["filters"]["ranges"]["bedrooms"], {"min": 1, "max": NO_MAXIMUM})
        self.assertEqual(payload["filters"]["enums"]["real_estate_type"], ["2"])


class ClearErrorsTest(unittest.TestCase):
    """Une valeur inconnue ou incohérente doit donner une erreur claire, jamais être ignorée."""

    def assert_error(self, message_part, **params):
        with self.assertRaises(ValueError) as error:
            run_search(**params)
        self.assertIn(message_part, str(error.exception))

    def test_unknown_property_type(self):
        self.assert_error("Valeurs acceptées : maison, appartement", property_type="studio")

    def test_min_greater_than_max(self):
        self.assert_error("surface_min (80) est plus grand que surface_max (40)", surface_min=80, surface_max=40)

    def test_negative_value(self):
        self.assert_error("rooms_min ne peut pas être négatif", rooms_min=-1)

    def test_unknown_region(self):
        self.assert_error("Région inconnu(e) : « COTE_AZUR »", region="COTE_AZUR")

    def test_unknown_department(self):
        self.assert_error("Département inconnu(e) : « ALPES_MARITIME »", department="ALPES_MARITIME")

    def test_unknown_category(self):
        self.assert_error("Catégorie inconnu(e) : « IMMO »", category="IMMO")

    def test_known_department_still_works(self):
        _, sent = run_search(department="alpes_maritimes")
        self.assertEqual(sent["locations"], [lbc.Department.ALPES_MARITIMES])

    def test_url_with_filters_is_refused(self):
        self.assert_error("Avec url", url="https://www.leboncoin.fr/recherche?category=9", surface_min=40)

    def test_city_alone_is_refused(self):
        self.assert_error("city seul ne localise pas", city="Antibes")


class CityFilterTest(unittest.TestCase):

    def test_removes_neighbouring_towns_and_counts_them(self):
        result, _ = run_search(city=["Antibes", "Juan-les-Pins"], **ANTIBES)
        cities = [ad["location"]["city"] for ad in result["ads"]]
        self.assertEqual(cities, ["Antibes 06600", "Juan-les-Pins 06160", "Antibes 06160"])
        self.assertEqual(result["removed_by_city_filter"], 3)  # Vallauris, Mougins, Cagnes

    def test_single_city_as_text(self):
        result, _ = run_search(city="Antibes", **ANTIBES)
        self.assertEqual(len(result["ads"]), 2)
        self.assertEqual(result["removed_by_city_filter"], 4)

    def test_ignores_case_accents_and_hyphens(self):
        self.assertTrue(city_matches("Juan-les-Pins 06160", ["JUAN LES PINS"]))
        self.assertTrue(city_matches("Cagnes-sur-Mer 06800", ["cagnes sur mer"]))
        self.assertTrue(city_matches("Vallauris 06220", ["VALLAURÎS"]))
        self.assertTrue(city_matches("Saint-Étienne 42000", ["saint etienne"]))

    def test_typographic_and_straight_apostrophes_are_the_same(self):
        self.assertTrue(city_matches("L’Escarène 06440", ["L'Escarène"]))
        self.assertTrue(city_matches("L'Escarène 06440", ["l’escarene"]))
        self.assertTrue(city_matches("Villeneuve-d’Ascq 59650", ["villeneuve d ascq"]))

    def test_does_not_match_a_different_city_with_same_start(self):
        self.assertFalse(city_matches("Cagnes-sur-Mer 06800", ["Cagnes"]))
        self.assertFalse(city_matches(None, ["Antibes"]))

    def test_without_city_nothing_is_removed(self):
        result, _ = run_search(**ANTIBES)
        self.assertEqual(len(result["ads"]), len(MIXED_CITIES))
        self.assertEqual(result["removed_by_city_filter"], 0)

    def test_radius_is_10_km_when_city_given(self):
        _, sent = run_search(city="Antibes", **ANTIBES)
        self.assertEqual(sent["locations"][0].radius, 10_000)

    def test_radius_unchanged_without_city(self):
        _, sent = run_search(**ANTIBES)
        self.assertEqual(sent["locations"][0].radius, 30_000)

    def test_explicit_radius_is_kept(self):
        _, sent = run_search(city="Antibes", radius=5_000, **ANTIBES)
        self.assertEqual(sent["locations"][0].radius, 5_000)

    def test_keep_only_cities_on_plain_dicts(self):
        ads = [{"location": {"city": "Antibes 06600"}}, {"location": {"city": "Mougins 06250"}}]
        kept, removed = keep_only_cities(ads, ["antibes"])
        self.assertEqual((len(kept), removed), (1, {"Mougins 06250": 1}))

    def test_removed_cities_tells_which_cities_were_removed(self):
        result, _ = run_search(ads_cities=MIXED_CITIES + ["Vallauris 06220"], city="Antibes", **ANTIBES)
        self.assertEqual(result["removed_cities"], {
            "Juan-les-Pins 06160": 1, "Vallauris 06220": 2, "Mougins 06250": 1, "Cagnes-sur-Mer 06800": 1})
        self.assertEqual(result["removed_by_city_filter"], 5)

    def test_ad_without_city_is_kept_and_marked(self):
        ads = [{"location": {"city": None}}, {"location": {"city": ""}}, {"location": {"city": "Antibes 06600"}}]
        kept, removed = keep_only_cities(ads, ["Antibes"])
        self.assertEqual(len(kept), 3)
        self.assertEqual(removed, {})
        self.assertEqual([ad.get("city_check") for ad in kept], ["ville inconnue", "ville inconnue", None])


class UserExampleTest(unittest.TestCase):
    """L'exemple de la demande : ne doit renvoyer que des annonces à Antibes."""

    def test_example_returns_only_antibes(self):
        result, sent = run_search(
            city="Antibes", category="IMMOBILIER_VENTES_IMMOBILIERES", property_type="appartement",
            surface_min=40, rooms_min=2, price_max=250000, **ANTIBES)
        self.assertTrue(result["ads"])
        for ad in result["ads"]:
            self.assertTrue(ad["location"]["city"].startswith("Antibes"))
        self.assertEqual(sent["real_estate_type"], ["2"])
        self.assertEqual(sent["square"], [40, NO_MAXIMUM])
        self.assertEqual(sent["rooms"], [2, NO_MAXIMUM])
        self.assertEqual(sent["price"], [0, 250000])
        self.assertEqual(sent["category"], lbc.Category.IMMOBILIER_VENTES_IMMOBILIERES)


class CityAndZipcodeMatchingTest(unittest.TestCase):

    def test_matches_on_city_name_when_label_is_missing(self):
        location = {"city": None, "city_name": "Juan-les-Pins", "zipcode": "06160"}
        self.assertTrue(location_matches(location, ["juan les pins"]))

    def test_matches_on_zipcode(self):
        location = {"city": "Antibes", "city_name": "Antibes", "zipcode": "06160"}
        self.assertTrue(location_matches(location, ["06160"]))
        self.assertFalse(location_matches({"city": "Vallauris 06220", "zipcode": "06220"}, ["06160"]))

    def test_zipcode_in_wanted_list_is_not_taken_for_a_city_name(self):
        self.assertFalse(city_matches("06600", ["06600"]))

    def test_label_with_other_format_still_matches_via_city_name(self):
        # Si search renvoie un city_label inattendu, city_name ou zipcode suffisent.
        location = {"city": "Antibes (06)", "city_name": "Antibes", "zipcode": "06600"}
        self.assertTrue(location_matches(location, ["Antibes"]))

    def test_removed_cities_uses_name_and_zipcode_when_label_missing(self):
        ads = [{"location": {"city": None, "city_name": "Vallauris", "zipcode": "06220"}}]
        kept, removed = keep_only_cities(ads, ["Antibes"])
        self.assertEqual((kept, removed), ([], {"Vallauris 06220": 1}))

    def test_ad_with_nothing_is_kept_and_marked(self):
        ads = [{"location": {"city": None, "city_name": None, "zipcode": None}}]
        kept, _ = keep_only_cities(ads, ["Antibes"])
        self.assertEqual(kept[0]["city_check"], "ville inconnue")

    def test_real_search_format_with_district(self):
        # Format réel relevé dans les résultats de search le 12/11/2026 :
        # le quartier vient après le code postal, et le nom seul est dans city_name.
        ads = [{"location": {"city": "Antibes 06600 Coeur de ville", "city_name": "Antibes", "zipcode": "06600"}},
               {"location": {"city": "Antibes 06160 Juan les Pins", "city_name": "Antibes", "zipcode": "06160"}},
               {"location": {"city": "Vallauris 06220 Golfe-Juan", "city_name": "Vallauris", "zipcode": "06220"}}]
        kept, removed = keep_only_cities(ads, ["Antibes", "Juan-les-Pins"])
        self.assertEqual(len(kept), 2)
        self.assertEqual(removed, {"Vallauris 06220 Golfe-Juan": 1})

    def test_search_ads_exposes_city_name(self):
        result, _ = run_search(**ANTIBES)
        self.assertIn("city_name", result["ads"][0]["location"])


if __name__ == "__main__":
    unittest.main()

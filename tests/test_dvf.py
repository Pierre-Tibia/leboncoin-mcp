"""Tests du tool price_vs_market.

Lancer depuis la racine du projet :  python -m unittest discover tests

La plupart des tests utilisent de fausses ventes et ne demandent pas Internet.
Le test « Antibes » interroge les vraies données DVF (ou le cache local) ;
il est ignoré si le réseau est indisponible.
"""

import gzip
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import dvf
from dvf import Sale, _compare, _extract_sales, _radii, price_vs_market

# Point de référence : centre d'Antibes.
LAT, LNG = 43.58, 7.125


def sale_at(distance_m: float, price_per_m2: float, day: str = "2025-06-01",
            property_type: str = "Appartement", surface: float = 50.0) -> Sale:
    """Fausse vente située `distance_m` mètres au nord du point de référence."""
    lat, lng = dvf._point_at(LAT, LNG, distance_m, 0)
    return Sale(day, lat, lng, property_type, price_per_m2, surface)


def dvf_row(mutation: str, type_local: str, surface: str = "50", value: str = "250000",
            nature: str = "Vente", parcelle: str = "P1", lot: str = "1", pieces: str = "2") -> dict:
    """Fausse ligne brute, avec les colonnes DVF utilisées par le code."""
    return {
        "id_mutation": mutation, "date_mutation": "2025-03-01", "nature_mutation": nature,
        "valeur_fonciere": value, "id_parcelle": parcelle, "lot1_numero": lot,
        "type_local": type_local, "surface_reelle_bati": surface if type_local else "",
        "nombre_pieces_principales": pieces, "latitude": str(LAT), "longitude": str(LNG),
    }


class ExtractSalesTest(unittest.TestCase):
    """Nettoyage des lignes DVF brutes."""

    def test_single_apartment_is_kept(self):
        sales = _extract_sales([dvf_row("M1", "Appartement", surface="50", value="250000")])
        self.assertEqual(len(sales), 1)
        self.assertEqual(sales[0].price_per_m2, 5000)
        self.assertEqual(sales[0].surface, 50)

    def test_apartment_with_outbuildings_is_kept(self):
        # Appartement + cave + parking : le prix total reste celui du logement.
        rows = [dvf_row("M1", "Appartement"),
                dvf_row("M1", "Dépendance", lot="2"),
                dvf_row("M1", "Dépendance", lot="3")]
        self.assertEqual(len(_extract_sales(rows)), 1)

    def test_sale_of_several_housings_is_rejected(self):
        rows = [dvf_row("M1", "Appartement", lot="1"), dvf_row("M1", "Appartement", lot="2")]
        self.assertEqual(_extract_sales(rows), [])

    def test_house_repeated_on_several_rows_counts_once(self):
        # DVF répète la maison sur une ligne par nature de terrain.
        rows = [dvf_row("M1", "Maison", lot=""), dvf_row("M1", "Maison", lot=""),
                dvf_row("M1", "", lot="")]
        self.assertEqual(len(_extract_sales(rows)), 1)

    def test_housing_with_shop_is_rejected(self):
        rows = [dvf_row("M1", "Appartement"),
                dvf_row("M1", "Local industriel. commercial ou assimilé", lot="2")]
        self.assertEqual(_extract_sales(rows), [])

    def test_non_sale_mutations_are_rejected(self):
        self.assertEqual(_extract_sales([dvf_row("M1", "Appartement", nature="Echange")]), [])

    def test_absurd_prices_are_rejected(self):
        rows = [dvf_row("M1", "Appartement", value="1"),            # vente à 1 €
                dvf_row("M2", "Appartement", value="5000000"),      # 100 000 €/m²
                dvf_row("M3", "Appartement", surface="0")]          # surface inconnue
        self.assertEqual(_extract_sales(rows), [])


class CompareTest(unittest.TestCase):
    """Rayon adaptatif, médiane, écart et alerte de fiabilité (sans Internet)."""

    def test_median_and_gap(self):
        sales = [sale_at(100, price) for price in range(4000, 6200, 200)]  # 11 ventes, médiane 5000
        result = _compare(sales, LAT, LNG, 4000, 1000, 3)
        self.assertEqual(result["median_price_per_m2"], 5000)
        self.assertEqual(result["gap_percent"], -20.0)
        self.assertEqual(result["radius_m"], 500)
        self.assertEqual(result["sales_count"], 11)
        self.assertTrue(result["reliable"])
        self.assertNotIn("warning", result)

    def test_latest_sale_date_is_among_sales_used(self):
        # La vente la plus récente (décembre) est à 1,5 km : hors rayon, donc
        # elle ne doit pas être annoncée comme « dernière vente utilisée ».
        sales = [sale_at(100, 5000, "2025-03-01")] * 9 + [sale_at(100, 5000, "2025-06-15")]
        sales += [sale_at(1500, 5000, "2025-12-31")]
        result = _compare(sales, LAT, LNG, 5000, 1000, 3)
        self.assertEqual(result["latest_sale_date"], "2025-06-15")
        self.assertEqual(result["period"]["to"], "2025-12-31")

    def test_latest_sale_date_is_none_without_sales(self):
        self.assertIsNone(_compare([], LAT, LNG, 5000, 1000, 3)["latest_sale_date"])

    def test_radius_grows_until_10_sales(self):
        sales = [sale_at(300, 5000)] * 6 + [sale_at(700, 5000)] * 4 + [sale_at(900, 5000)] * 5
        result = _compare(sales, LAT, LNG, 5000, 1000, 3)
        self.assertEqual(result["radius_m"], 750)
        self.assertEqual(result["sales_count"], 10)

    def test_fewer_than_10_sales_is_flagged_unreliable(self):
        sales = [sale_at(200, 5000)] * 4 + [sale_at(1500, 5000)] * 20  # trop loin
        result = _compare(sales, LAT, LNG, 5000, 1000, 3)
        self.assertEqual(result["radius_m"], 1000)
        self.assertEqual(result["sales_count"], 4)
        self.assertFalse(result["reliable"])
        self.assertIn("PEU FIABLE", result["warning"])

    def test_max_radius_parameter_is_respected(self):
        sales = [sale_at(1400, 5000)] * 10
        self.assertEqual(_compare(sales, LAT, LNG, 5000, 1000, 3)["sales_count"], 0)
        result = _compare(sales, LAT, LNG, 5000, 1500, 3)
        self.assertEqual(result["radius_m"], 1500)
        self.assertEqual(result["sales_count"], 10)

    def test_period_counts_back_from_latest_sale(self):
        sales = [sale_at(100, 5000, "2025-12-31")] * 10 + [sale_at(100, 9000, "2022-06-01")] * 10
        result = _compare(sales, LAT, LNG, 5000, 1000, 3)
        self.assertEqual(result["period"], {"from": "2023-01-01", "to": "2025-12-31"})
        self.assertEqual(result["sales_count"], 10)  # les ventes de 2022 sont exclues
        longer = _compare(sales, LAT, LNG, 5000, 1000, 4)
        self.assertEqual(longer["sales_count"], 20)

    def test_no_sales_at_all(self):
        result = _compare([], LAT, LNG, 5000, 1000, 3)
        self.assertIsNone(result["median_price_per_m2"])
        self.assertFalse(result["reliable"])

    def test_radius_steps(self):
        self.assertEqual(_radii(1000), [500, 750, 1000])
        self.assertEqual(_radii(1100), [500, 750, 1000, 1100])
        self.assertEqual(_radii(300), [300])


class PriceVsMarketTest(unittest.TestCase):
    """Le tool complet, avec les accès Internet remplacés par de fausses données."""

    def setUp(self):
        fake_sales = ([sale_at(100, 5000)] * 12
                      + [sale_at(100, 9000, property_type="Maison")] * 12)
        patches = [
            mock.patch.object(dvf, "_communes_around", return_value=(("06004", "06"), {("06004", "06")})),
            mock.patch.object(dvf, "_available_years", return_value=(2021, 2022, 2023, 2024, 2025)),
            mock.patch.object(dvf, "_commune_sales", return_value=tuple(fake_sales)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_only_same_property_type_is_used(self):
        self.assertEqual(price_vs_market(LAT, LNG, "appartement", 5000)["median_price_per_m2"], 5000)
        self.assertEqual(price_vs_market(LAT, LNG, "Maison", 9000)["median_price_per_m2"], 9000)

    def test_rejects_invalid_parameters(self):
        for kwargs in ({"property_type": "studio"}, {"price_per_m2": 0}, {"max_radius_m": 0},
                       {"max_radius_m": 10_000}, {"years": 0}, {"years": 6}, {"lat": 123.0},
                       {"surface_m2": 0}, {"surface_m2": -20}):
            params = {"lat": LAT, "lng": LNG, "property_type": "appartement", "price_per_m2": 5000} | kwargs
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                price_vs_market(**params)

    def test_rejects_alsace_moselle(self):
        with mock.patch.object(dvf, "_communes_around", return_value=(("57463", "57"), {("57463", "57")})):
            with self.assertRaisesRegex(ValueError, "Moselle"):
                price_vs_market(49.1193, 6.1757, "appartement", 2000)

    def test_rejects_points_at_sea(self):
        with mock.patch.object(dvf, "_communes_around", return_value=(None, set())):
            with self.assertRaises(ValueError):
                price_vs_market(43.0, 7.5, "appartement", 5000)


class SurfaceFilterTest(unittest.TestCase):
    """Paramètre surface_m2 : ne comparer qu'aux ventes de surface comparable."""

    # Quartier type : beaucoup de studios chers au m², quelques grands appartements.
    STUDIOS = [sale_at(100, 7000, surface=25)] * 15
    LARGE = [sale_at(100, 4800, surface=60), sale_at(100, 5000, surface=70),
             sale_at(100, 5200, surface=85)] * 4  # 12 ventes de 60 à 85 m²

    def test_without_surface_result_is_unchanged(self):
        # Même appel avec surface_m2=None ou sans le paramètre : réponse identique,
        # sans aucun nouveau champ.
        sales = self.STUDIOS + self.LARGE
        explicit_none = _compare(sales, LAT, LNG, 5000, 1000, 3, surface_m2=None)
        self.assertEqual(explicit_none, _compare(sales, LAT, LNG, 5000, 1000, 3))
        self.assertNotIn("all_sizes", explicit_none)
        self.assertNotIn("surface_range_m2", explicit_none)
        self.assertEqual(explicit_none["sales_count"], 27)

    def test_large_flat_no_longer_looks_cheap(self):
        result = _compare(self.STUDIOS + self.LARGE, LAT, LNG, 5000, 1000, 3, surface_m2=70)
        # Toutes surfaces : la médiane est tirée vers le haut par les studios.
        self.assertEqual(result["all_sizes"]["median_price_per_m2"], 7000)
        self.assertEqual(result["all_sizes"]["gap_percent"], -28.6)
        self.assertEqual(result["all_sizes"]["sales_count"], 27)
        # Surfaces comparables (49–91 m²) : le bien est au prix du marché.
        self.assertEqual(result["median_price_per_m2"], 5000)
        self.assertEqual(result["gap_percent"], 0.0)
        self.assertEqual(result["sales_count"], 12)
        self.assertEqual(result["surface_range_m2"], {"min": 49.0, "max": 91.0})

    def test_bounds_70_and_130_percent_are_included(self):
        sales = ([sale_at(100, 5000, surface=70)] * 5 + [sale_at(100, 5000, surface=130)] * 5
                 + [sale_at(100, 9000, surface=69.9)] * 5 + [sale_at(100, 9000, surface=130.1)] * 5)
        result = _compare(sales, LAT, LNG, 5000, 1000, 3, surface_m2=100)
        self.assertEqual(result["sales_count"], 10)
        self.assertEqual(result["median_price_per_m2"], 5000)

    def test_radius_grows_on_comparable_sales_count(self):
        # 15 studios à 100 m suffiraient sans filtre ; les 10 ventes comparables
        # ne sont atteintes qu'à 1 000 m.
        sales = self.STUDIOS + [sale_at(600, 5000, surface=70)] * 4 + [sale_at(900, 5000, surface=70)] * 6
        self.assertEqual(_compare(sales, LAT, LNG, 5000, 1000, 3)["radius_m"], 500)
        result = _compare(sales, LAT, LNG, 5000, 1000, 3, surface_m2=70)
        self.assertEqual(result["radius_m"], 1000)
        self.assertEqual(result["sales_count"], 10)
        self.assertTrue(result["reliable"])
        # La médiane toutes surfaces est calculée au même rayon (1 000 m).
        self.assertEqual(result["all_sizes"]["sales_count"], 25)

    def test_fewer_than_10_comparable_sales_is_unreliable(self):
        sales = self.STUDIOS + [sale_at(100, 5000, surface=70)] * 9
        result = _compare(sales, LAT, LNG, 5000, 1000, 3, surface_m2=70)
        self.assertEqual(result["radius_m"], 1000)
        self.assertEqual(result["sales_count"], 9)
        self.assertFalse(result["reliable"])
        self.assertIn("PEU FIABLE", result["warning"])
        self.assertIn("49–91 m²", result["warning"])

    def test_latest_sale_date_comes_from_comparable_sales(self):
        sales = [sale_at(100, 5000, "2025-03-01", surface=70)] * 10 + [sale_at(100, 7000, "2025-12-31", surface=25)]
        result = _compare(sales, LAT, LNG, 5000, 1000, 3, surface_m2=70)
        self.assertEqual(result["latest_sale_date"], "2025-03-01")

    def test_no_sales_at_all_with_surface(self):
        result = _compare([], LAT, LNG, 5000, 1000, 3, surface_m2=70)
        self.assertFalse(result["reliable"])
        self.assertEqual(result["all_sizes"]["sales_count"], 0)


# Fichier DVF minimal : une vente d'appartement de 50 m² à 250 000 €.
CSV_HEADER = ("id_mutation,date_mutation,nature_mutation,valeur_fonciere,id_parcelle,lot1_numero,"
              "type_local,surface_reelle_bati,nombre_pieces_principales,latitude,longitude\n")
CSV_ONE_SALE = (CSV_HEADER + f"M1,2025-03-01,Vente,250000,P1,1,Appartement,50,2,{LAT},{LNG}\n").encode()
CSV_TWO_SALES = CSV_ONE_SALE + f"M2,2025-11-20,Vente,300000,P2,1,Appartement,50,2,{LAT},{LNG}\n".encode()
DVF_LISTING = b'<a href="/geo-dvf/latest/csv/2024/">2024</a> <a href="/geo-dvf/latest/csv/2025/">2025</a>'
DVF_LISTING_2026 = DVF_LISTING + b' <a href="/geo-dvf/latest/csv/2026/">2026</a>'


class CacheExpiryTest(unittest.TestCase):
    """Expiration du cache à 30 jours, avec un dossier de cache temporaire
    et un faux accès Internet (_http_get remplacé)."""

    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.cache = Path(temp_dir.name)
        for name, value in (("CACHE_DIR", self.cache), ("YEARS_CACHE_FILE", self.cache / "years.json")):
            patch = mock.patch.object(dvf, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def fake_internet(self, response=None, offline=False):
        """Remplace _http_get. Renvoie le faux pour compter les appels."""
        effect = RuntimeError("pas d'Internet") if offline else None
        patch = mock.patch.object(dvf, "_http_get", return_value=response, side_effect=effect)
        self.addCleanup(patch.stop)
        return patch.start()

    def make_old(self, path: Path, days: int):
        old = time.time() - days * 24 * 3600
        os.utime(path, (old, old))

    # --- fichiers de ventes

    def test_missing_file_is_downloaded_and_cached(self):
        http = self.fake_internet(CSV_ONE_SALE)
        self.assertEqual(len(dvf._commune_sales("06004", 2025)), 1)
        self.assertTrue((self.cache / "2025" / "06004.csv.gz").exists())
        http.assert_called_once()

    def test_fresh_file_is_not_downloaded_again(self):
        self.fake_internet(CSV_ONE_SALE)
        dvf._commune_sales("06004", 2025)
        self.make_old(self.cache / "2025" / "06004.csv.gz", days=29)
        http = self.fake_internet(CSV_TWO_SALES)
        self.assertEqual(len(dvf._commune_sales("06004", 2025)), 1)  # toujours l'ancien contenu
        http.assert_not_called()

    def test_file_older_than_30_days_is_downloaded_again(self):
        self.fake_internet(CSV_ONE_SALE)
        dvf._commune_sales("06004", 2025)
        self.make_old(self.cache / "2025" / "06004.csv.gz", days=31)
        http = self.fake_internet(CSV_TWO_SALES)
        self.assertEqual(len(dvf._commune_sales("06004", 2025)), 2)  # nouvelle vente vue
        http.assert_called_once()

    def test_expired_file_is_kept_when_offline(self):
        self.fake_internet(CSV_ONE_SALE)
        dvf._commune_sales("06004", 2025)
        self.make_old(self.cache / "2025" / "06004.csv.gz", days=31)
        self.fake_internet(offline=True)
        self.assertEqual(len(dvf._commune_sales("06004", 2025)), 1)

    def test_offline_without_cache_raises(self):
        self.fake_internet(offline=True)
        with self.assertRaises(RuntimeError):
            dvf._commune_sales("06004", 2025)

    def test_commune_absent_from_dvf_is_rechecked_after_30_days(self):
        self.fake_internet(None)  # 404 : pas de fichier cette année-là
        self.assertEqual(dvf._commune_sales("06004", 2025), ())
        self.make_old(self.cache / "2025" / "06004.none", days=31)
        self.fake_internet(CSV_ONE_SALE)  # le fichier a été publié depuis
        self.assertEqual(len(dvf._commune_sales("06004", 2025)), 1)
        self.assertFalse((self.cache / "2025" / "06004.none").exists())

    # --- liste des années

    def test_new_year_is_detected_after_30_days(self):
        self.fake_internet(DVF_LISTING)
        self.assertEqual(dvf._available_years(), (2024, 2025))
        self.make_old(self.cache / "years.json", days=31)
        self.fake_internet(DVF_LISTING_2026)
        self.assertEqual(dvf._available_years(), (2024, 2025, 2026))

    def test_years_list_is_not_rechecked_before_30_days(self):
        self.fake_internet(DVF_LISTING)
        dvf._available_years()
        self.make_old(self.cache / "years.json", days=29)
        http = self.fake_internet(DVF_LISTING_2026)
        self.assertEqual(dvf._available_years(), (2024, 2025))
        http.assert_not_called()

    def test_expired_years_list_is_kept_when_offline(self):
        self.fake_internet(DVF_LISTING)
        dvf._available_years()
        self.make_old(self.cache / "years.json", days=31)
        self.fake_internet(offline=True)
        self.assertEqual(dvf._available_years(), (2024, 2025))


class RealDataTest(unittest.TestCase):
    """Exemple demandé : appartement au centre d'Antibes affiché à 4 000 €/m²."""

    def test_antibes_center(self):
        try:
            result = price_vs_market(43.58, 7.125, "appartement", 4000)
        except RuntimeError as error:  # pas d'Internet et pas de cache
            self.skipTest(f"Données DVF inaccessibles : {error}")
        self.assertTrue(4000 <= result["median_price_per_m2"] <= 8000, result)
        expected_gap = (4000 - result["median_price_per_m2"]) / result["median_price_per_m2"] * 100
        self.assertAlmostEqual(result["gap_percent"], expected_gap, delta=0.1)
        self.assertIn(result["radius_m"], (500, 750, 1000))
        self.assertGreaterEqual(result["sales_count"], 10)
        self.assertTrue(result["reliable"])
        self.assertTrue(result["period"]["from"] <= result["latest_sale_date"] <= result["period"]["to"])

    def test_antibes_center_with_surface(self):
        try:
            result = price_vs_market(43.58, 7.125, "appartement", 4000, surface_m2=67)
        except RuntimeError as error:
            self.skipTest(f"Données DVF inaccessibles : {error}")
        self.assertTrue(4000 <= result["median_price_per_m2"] <= 8000, result)
        self.assertGreaterEqual(result["sales_count"], 10)
        self.assertLess(result["sales_count"], result["all_sizes"]["sales_count"])
        self.assertEqual(result["surface_range_m2"], {"min": 46.9, "max": 87.1})


if __name__ == "__main__":
    unittest.main()

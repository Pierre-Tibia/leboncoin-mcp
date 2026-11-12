"""Tests du mode compact de search_ads (compact_ad.py).

Lancer depuis la racine du projet :  python -m unittest discover tests

Leboncoin n'est jamais appelé. Les fausses annonces reprennent les formats relevés
lors des mesures du 12/11/2026 (libellés, valeurs « 40 m² », « 1 ch. », quartier après le code postal).
"""

import json
import unittest
from types import SimpleNamespace
from unittest import mock

import server
from compact_ad import compact_ad, district_from_label, find_alerts, find_text_alerts

FIELDS = {"id", "title", "price", "url", "city", "zipcode", "district", "lat", "lng",
          "surface", "rooms", "bedrooms", "seller", "published", "alerts"}


def attribute(key, value, key_label=None, value_label=None):
    return SimpleNamespace(key=key, key_label=key_label, value=value, value_label=value_label or value)


def realistic_ad(ad_id=3283982572, subject="Appartement 2 pièces 40 m²", body=None, attributes=None,
                 owner_type="private", city_label="Antibes 06160 Juan-les-Pins"):
    """Annonce de taille réelle : texte de 1 700 caractères, 12 images, 40 attributs."""
    if attributes is None:
        attributes = [
            attribute("real_estate_type", "2", "Type de bien", "Appartement"),
            attribute("square", "40", "Surface habitable", "40 m²"),
            attribute("rooms", "2", "Nombre de pièces", "2"),
            attribute("bedrooms", "1", "Nombre de chambres", "1 ch."),
            attribute("floor_number", "2", "Étage de votre bien", "2"),
            attribute("immo_sell_type", "old"),
        ] + [attribute(f"extra_{i}", "x" * 30, f"Attribut {i}") for i in range(34)]
    parts = city_label.split()  # « Antibes 06160 Juan-les-Pins » → ville, code postal, quartier
    location = SimpleNamespace(city_label=city_label, city=parts[0] if parts else None,
                               zipcode=parts[1] if len(parts) > 1 else None,
                               department_name="Alpes-Maritimes", region_name="Provence-Alpes-Côte d'Azur",
                               lat=43.56974, lng=7.10921)
    return SimpleNamespace(
        id=ad_id, subject=subject, price=245000,
        url=f"https://www.leboncoin.fr/ad/ventes_immobilieres/{ad_id}",
        category_name="Ventes immobilières", ad_type="offer",
        body=body if body is not None else ("Bel appartement lumineux proche des plages. " * 40)[:1700],
        images=[f"https://img.leboncoin.fr/api/v1/lbcpb1/images/{i:02x}/c7/03/"
                f"b4c70351605bcfa4a6e4180e492739042d68e43f.jpg?rule=ad-large" for i in range(12)],
        first_publication_date="2026-10-07 15:46:06", location=location,
        attributes={a.key: a for a in attributes}, has_phone=False,
        raw={"owner": {"type": owner_type}} if owner_type else {},
    )


def size(data) -> int:
    return len(json.dumps(data, ensure_ascii=False))


class CompactSizeTest(unittest.TestCase):

    def test_compact_ad_is_under_600_characters(self):
        ad = realistic_ad()
        full_size, compact_size = size(server._ad_to_dict(ad)), size(compact_ad(ad))
        print(f"\n  annonce complète : {full_size} car., compacte : {compact_size} car. "
              f"({100 - 100 * compact_size // full_size} % de moins)")
        self.assertLess(compact_size, 600)
        self.assertLess(compact_size * 5, full_size)


class CompactFieldsTest(unittest.TestCase):

    def test_exactly_the_15_requested_fields(self):
        self.assertEqual(set(compact_ad(realistic_ad())), FIELDS)

    def test_values(self):
        compact = compact_ad(realistic_ad())
        self.assertEqual(compact["surface"], 40)    # « 40 m² »
        self.assertEqual(compact["rooms"], 2)
        self.assertEqual(compact["bedrooms"], 1)    # « 1 ch. »
        self.assertEqual(compact["city"], "Antibes")
        self.assertEqual(compact["zipcode"], "06160")
        self.assertEqual(compact["district"], "Juan-les-Pins")
        self.assertEqual(compact["published"], "2026-10-07 15:46:06")
        self.assertEqual(compact["alerts"], [])

    def test_values_found_by_label_when_key_differs(self):
        ad = realistic_ad(attributes=[attribute("autre_cle", "", "Surface habitable", "52 m²")])
        self.assertEqual(compact_ad(ad)["surface"], 52)

    def test_missing_attributes_give_none(self):
        compact = compact_ad(realistic_ad(attributes=[]))
        self.assertEqual((compact["surface"], compact["rooms"], compact["bedrooms"]), (None, None, None))

    def test_district(self):
        self.assertEqual(district_from_label("Antibes 06600 Coeur de ville"), "Coeur de ville")
        self.assertIsNone(district_from_label("Antibes 06600"))
        self.assertIsNone(district_from_label(None))

    def test_seller_type(self):
        self.assertEqual(compact_ad(realistic_ad(owner_type="pro"))["seller"], "pro")
        self.assertEqual(compact_ad(realistic_ad(owner_type="private"))["seller"], "particulier")
        self.assertEqual(compact_ad(realistic_ad(owner_type=None))["seller"], "inconnu")


class TextAlertsTest(unittest.TestCase):
    """Une alerte par cas, plus majuscules/accents, mots entiers, négations, titre seul, texte seul."""

    def assert_alerts(self, body, expected, title=""):
        self.assertEqual(find_text_alerts(title, body), expected)

    def test_a_renover(self):
        for text in ["Appartement à rénover", "rénovation à prévoir", "à rafraîchir",
                     "rafraîchissement à prévoir", "à restaurer", "à moderniser", "idéal pour rénover"]:
            with self.subTest(text=text):
                self.assert_alerts(text, ["à rénover"])

    def test_travaux(self):
        self.assert_alerts("Prévoir des travaux de cuisine", ["travaux"])

    def test_travaux_negations_are_ignored(self):
        for text in ["Vendu meublé, sans travaux supplémentaires", "aucun travaux", "aucuns travaux",
                     "pas de travaux à prévoir"]:
            with self.subTest(text=text):
                self.assert_alerts(text, [])

    def test_travaux_after_a_negation_elsewhere_still_counts(self):
        self.assert_alerts("Sans vis-à-vis. Gros travaux de toiture votés", ["travaux"])

    def test_viager(self):
        for text in ["Vente en viager occupé", "rente viagère", "nue-propriété"]:
            with self.subTest(text=text):
                self.assertIn("viager", find_text_alerts("", text))

    def test_occupe(self):
        self.assert_alerts("Bien occupé", ["occupé"])
        self.assert_alerts("vendu occupée", ["occupé"])
        self.assert_alerts("Locataire en place", ["occupé"])

    def test_loue(self):
        self.assert_alerts("Appartement loué 750 €", ["loué"])
        self.assert_alerts("bail en cours jusqu'en 2027", ["loué"])

    def test_rez_de_chaussee(self):
        for text in ["Situé au rez-de-chaussée", "RDC sur jardin", "en rez-de-jardin"]:
            with self.subTest(text=text):
                self.assert_alerts(text, ["rez-de-chaussée"])

    def test_ignores_case_and_accents(self):
        self.assert_alerts("À RÉNOVER, VENDU LOUÉ", ["à rénover", "loué"])

    def test_whole_words_only(self):
        # « louer », « louez », « occuper » contiennent loue/occupe mais ne sont pas les mêmes mots.
        self.assert_alerts("Idéal à louer, louez-le facilement ; occuper l'espace", [])

    def test_found_in_title_only(self):
        self.assertEqual(find_text_alerts("Viager occupé Juan-les-Pins", "Bel appartement."),
                         ["viager", "occupé"])

    def test_found_in_body_only(self):
        self.assertEqual(find_text_alerts("Appartement 2 pièces", "Travaux à prévoir"), ["travaux"])

    def test_several_alerts_in_stable_order(self):
        self.assert_alerts("RDC, loué, à rénover, travaux", ["à rénover", "travaux", "loué", "rez-de-chaussée"])


class NewAlertsTest(unittest.TestCase):
    """Alertes ajoutées après relecture des annonces réelles du 12/11/2026."""

    def test_renovation_expressions(self):
        # Passage réel de l'annonce 3301613193.
        for text in ["L'appartement nécessite une rénovation intérieure.", "besoin de rénovation"]:
            with self.subTest(text=text):
                self.assertEqual(find_text_alerts("", text), ["à rénover"])

    def test_renovation_alone_is_not_an_alert(self):
        self.assertEqual(find_text_alerts("", "Rénovation complète en 2020, entièrement rénové"), [])

    def test_sous_compromis(self):
        # Passage réel de l'annonce 3260557108.
        for text in ["Appartement 2 pièces 40 m² Sous compromis : Découvrez", "Bien sous offre", "offre acceptée"]:
            with self.subTest(text=text):
                self.assertEqual(find_text_alerts("", text), ["sous compromis"])

    def test_encheres(self):
        for text in ["À vendre en IMMO-INTERACTIF, enchère en ligne gratuite", "vente aux enchères"]:
            with self.subTest(text=text):
                self.assertEqual(find_text_alerts("", text), ["enchères"])

    def test_sans_ascenseur(self):
        # Passages réels des annonces 3295219746, 3287442153, 3291514204.
        for text in ["situé au premier étage d'une résidence sécurisée sans ascenseur",
                     "l'immeuble ne dispose pas d'ascenseur", "pas d’ascenseur"]:
            with self.subTest(text=text):
                self.assertEqual(find_text_alerts("", text), ["sans ascenseur"])

    def test_typographic_apostrophe_becomes_a_space(self):
        self.assertEqual(find_text_alerts("", "Il n’y a pas d’ascenseur, appartement loué"), ["loué", "sans ascenseur"])

    def test_with_elevator_is_not_an_alert(self):
        self.assertEqual(find_text_alerts("", "Situé au 3ème étage avec ascenseur"), [])


class AttributeAlertsTest(unittest.TestCase):

    def test_works_from_condition_attribute(self):
        ad = realistic_ad(attributes=[attribute("global_condition", "4", "État du bien", "Travaux à prévoir")])
        self.assertEqual(find_alerts(ad), ["travaux"])

    def test_good_condition_gives_no_alert(self):
        ad = realistic_ad(attributes=[attribute("global_condition", "2", "État du bien", "Bon état")])
        self.assertEqual(find_alerts(ad), [])

    def test_no_elevator_from_attribute_label(self):
        ad = realistic_ad(attributes=[attribute("elevator", "2", "Ascenseur", "Non")])
        self.assertEqual(find_alerts(ad), ["sans ascenseur"])

    def test_elevator_yes_gives_no_alert(self):
        ad = realistic_ad(attributes=[attribute("elevator", "1", "Ascenseur", "Oui")])
        self.assertEqual(find_alerts(ad), [])

    def test_viager_from_sale_type(self):
        ad = realistic_ad(attributes=[attribute("immo_sell_type", "viager")])
        self.assertEqual(find_alerts(ad), ["viager"])

    def test_ground_floor_from_floor_zero(self):
        ad = realistic_ad(attributes=[attribute("floor_number", "0", "Étage de votre bien", "0")])
        self.assertEqual(find_alerts(ad), ["rez-de-chaussée"])

    def test_other_floor_gives_no_alert(self):
        ad = realistic_ad(attributes=[attribute("floor_number", "3", "Étage de votre bien", "3")])
        self.assertEqual(find_alerts(ad), [])

    def test_no_duplicate_when_text_and_attribute_agree(self):
        ad = realistic_ad(body="Vente en viager", attributes=[attribute("immo_sell_type", "viager")])
        self.assertEqual(find_alerts(ad), ["viager"])


class SearchAdsModeTest(unittest.TestCase):

    def search(self, **params):
        with mock.patch.object(server, "_client") as client:
            client.search.return_value = SimpleNamespace(
                total=2, total_pro=1, total_private=1, max_pages=1,
                ads=[realistic_ad(1), realistic_ad(2, city_label="Vallauris 06220 Golfe Juan")])
            return server.search_ads(city="Antibes", latitude=43.566, longitude=7.119, **params)

    def test_compact_by_default(self):
        result = self.search()
        self.assertEqual(len(result["ads"]), 1)
        self.assertEqual(set(result["ads"][0]), FIELDS)
        self.assertEqual(result["removed_cities"], {"Vallauris 06220 Golfe Juan": 1})

    def test_full_on_request(self):
        result = self.search(full=True)
        self.assertIn("body", result["ads"][0])
        self.assertIn("images", result["ads"][0])
        self.assertEqual(result["removed_by_city_filter"], 1)

    def test_unknown_city_mark_survives_compact_mode(self):
        with mock.patch.object(server, "_client") as client:
            client.search.return_value = SimpleNamespace(
                total=1, total_pro=0, total_private=1, max_pages=1,
                ads=[realistic_ad(3, city_label="")])
            result = server.search_ads(city="Antibes", latitude=43.566, longitude=7.119)
        self.assertEqual(result["ads"][0]["city_check"], "ville inconnue")


if __name__ == "__main__":
    unittest.main()

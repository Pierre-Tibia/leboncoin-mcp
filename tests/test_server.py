"""Tests du serveur MCP (server.py).

Lancer depuis la racine du projet :  python -m unittest discover tests

Leboncoin n'est pas appelé : le client lbc est remplacé par une fausse annonce.
"""

import unittest
from types import SimpleNamespace
from unittest import mock

import server

SELLER_ID = "844a5547-c5fb-41ee-b7dc-45c050a9ce35"


def fake_ad() -> SimpleNamespace:
    """Fausse annonce avec juste les champs lus par get_ad."""
    location = SimpleNamespace(city_label="Antibes 06600", zipcode="06600", department_name="Alpes-Maritimes",
                               region_name="Provence-Alpes-Côte d'Azur", lat=43.58, lng=7.12)
    return SimpleNamespace(
        id=3225428585, subject="Appartement 3 pièces 67 m²", price=265000,
        url="https://www.leboncoin.fr/ad/ventes_immobilieres/3225428585",
        category_name="Ventes immobilières", ad_type="offer", body="", images=[],
        first_publication_date="2026-10-20 08:01:29", location=location, attributes={},
        has_phone=True, favorites=11, _user_id=SELLER_ID,
    )


class GetAdTest(unittest.TestCase):
    def test_returns_seller_user_id_usable_by_get_user(self):
        with mock.patch.object(server, "_client") as client:
            client.get_ad.return_value = fake_ad()
            result = server.get_ad("3225428585")
            self.assertEqual(result["user_id"], SELLER_ID)

            # L'identifiant renvoyé doit pouvoir être passé tel quel à get_user.
            server.get_user(result["user_id"])
            client.get_user.assert_called_once_with(SELLER_ID)

    def test_existing_fields_are_unchanged(self):
        with mock.patch.object(server, "_client") as client:
            client.get_ad.return_value = fake_ad()
            result = server.get_ad("3225428585")
        self.assertEqual(result["price"], 265000)
        self.assertEqual(result["favorites"], 11)
        self.assertEqual(result["location"]["city"], "Antibes 06600")


if __name__ == "__main__":
    unittest.main()

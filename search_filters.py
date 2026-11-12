"""Filtres de recherche pour search_ads : immobilier, ville, validation des noms.

Tout est ici pour toucher le moins possible à server.py.
Aucune fonction de ce fichier n'appelle Leboncoin.
"""

import re
import unicodedata
from typing import Optional

# Codes Leboncoin du filtre « type de bien » (paramètre real_estate_type des URL).
PROPERTY_TYPES = {
    "maison": "1",
    "appartement": "2",
    "terrain": "3",
    "parking": "4",
    "autre": "5",
}

# La bibliothèque lbc exige un minimum ET un maximum : on met un maximum
# très grand quand l'utilisateur n'en donne pas (même astuce que pour le prix).
NO_MAXIMUM = 999_999_999


def lookup(mapping: dict, name: str, what: str):
    """Renvoie mapping[NAME] ou lève une erreur claire listant les valeurs acceptées."""
    key = name.upper()
    if key not in mapping:
        accepted = ", ".join(sorted(mapping))
        raise ValueError(f"{what} inconnu(e) : « {name} ». Valeurs acceptées : {accepted}.")
    return mapping[key]


def _range(name: str, minimum: Optional[int], maximum: Optional[int]) -> Optional[list[int]]:
    """Transforme un couple min/max en [min, max] pour Leboncoin, ou None si rien n'est donné."""
    if minimum is None and maximum is None:
        return None
    for label, value in ((f"{name}_min", minimum), (f"{name}_max", maximum)):
        if value is not None and value < 0:
            raise ValueError(f"{label} ne peut pas être négatif (reçu : {value}).")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError(f"{name}_min ({minimum}) est plus grand que {name}_max ({maximum}).")
    return [minimum or 0, NO_MAXIMUM if maximum is None else maximum]


def build_real_estate_filters(
    property_type: Optional[str] = None,
    surface_min: Optional[int] = None,
    surface_max: Optional[int] = None,
    rooms_min: Optional[int] = None,
    rooms_max: Optional[int] = None,
    bedrooms_min: Optional[int] = None,
) -> dict:
    """Construit les filtres immobiliers dans le format attendu par lbc.Client.search.

    Exemple : surface_min=40 donne {"square": [40, NO_MAXIMUM]}.
    """
    filters = {}
    if property_type is not None:
        code = PROPERTY_TYPES.get(property_type.strip().lower())
        if code is None:
            accepted = ", ".join(PROPERTY_TYPES)
            raise ValueError(f"Type de bien inconnu : « {property_type} ». Valeurs acceptées : {accepted}.")
        filters["real_estate_type"] = [code]

    for key, name, minimum, maximum in (
        ("square", "surface", surface_min, surface_max),
        ("rooms", "rooms", rooms_min, rooms_max),
        ("bedrooms", "bedrooms", bedrooms_min, None),
    ):
        values = _range(name, minimum, maximum)
        if values:
            filters[key] = values
    return filters


def _normalize(text: str) -> str:
    """« Juan-les-Pins » et « juan les pins » deviennent identiques : sans accents, minuscules, sans tirets."""
    without_accents = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(re.split(r"[\s\-']+", without_accents.lower())).strip()


def city_names(city: Optional[str | list[str]]) -> list[str]:
    """Accepte une ville ("Antibes") ou plusieurs (["Antibes", "Juan-les-Pins"])."""
    if city is None:
        return []
    names = [city] if isinstance(city, str) else list(city)
    return [name for name in names if name and name.strip()]


def city_matches(city_label: Optional[str], wanted: list[str]) -> bool:
    """city_label vient de Leboncoin, par exemple « Antibes 06600 » : on enlève le code postal."""
    if not city_label:
        return False
    name = re.sub(r"\s*\d{5}\s*$", "", city_label)
    return _normalize(name) in {_normalize(w) for w in wanted if not _is_zipcode(w)}


def _is_zipcode(text: str) -> bool:
    return re.fullmatch(r"\d{5}", text.strip()) is not None


def location_matches(location: dict, wanted: list[str]) -> bool:
    """Compare sur les trois champs de Leboncoin : city_label (« Antibes 06600 »),
    city (« Antibes ») et zipcode (« 06600 »). wanted peut contenir des noms ou des codes postaux."""
    zipcodes = {w.strip() for w in wanted if _is_zipcode(w)}
    return (city_matches(location.get("city"), wanted)
            or city_matches(location.get("city_name"), wanted)
            or (location.get("zipcode") or "").strip() in zipcodes)


UNKNOWN_CITY = "ville inconnue"


def _location_label(location: dict) -> Optional[str]:
    """Nom lisible pour removed_cities, quel que soit le champ rempli par Leboncoin."""
    if location.get("city"):
        return location["city"]
    parts = [location.get("city_name"), location.get("zipcode")]
    return " ".join(p for p in parts if p) or None


def keep_only_cities(ads: list[dict], wanted: list[str]) -> tuple[list[dict], dict[str, int]]:
    """Garde les annonces situées dans une des villes voulues.

    Renvoie (annonces gardées, villes retirées avec leur nombre), par exemple {"Vallauris 06220": 4}.
    Une annonce sans ville ni code postal n'est pas retirée : elle est gardée et marquée « ville inconnue ».
    """
    kept, removed = [], {}
    for ad in ads:
        location = ad["location"]
        label = _location_label(location)
        if not label:
            ad["city_check"] = UNKNOWN_CITY
            kept.append(ad)
        elif location_matches(location, wanted):
            kept.append(ad)
        else:
            removed[label] = removed.get(label, 0) + 1
    return kept, removed


"""Version courte d'une annonce pour search_ads, et repérage des alertes.

But : environ 10 fois moins de tokens par annonce. Le texte complet reste disponible avec get_ad.
Aucune fonction de ce fichier n'appelle Leboncoin.
"""

import re
import unicodedata
from typing import Any, Optional

# Expressions cherchées dans le titre et le texte, après normalisation
# (minuscules, sans accents, ponctuation remplacée par des espaces). Mots entiers seulement.
ALERT_PHRASES = {
    "à rénover": ["a renover", "renover", "renovation a prevoir", "a rafraichir",
                  "rafraichissement a prevoir", "a restaurer", "a moderniser",
                  "necessite une renovation", "renovation interieure", "besoin de renovation"],
    "travaux": [],  # cherché à part : voir TRAVAUX_PATTERN (négations exclues)
    "viager": ["viager", "viagere", "nue propriete"],
    "occupé": ["occupe", "occupee", "locataire en place"],
    "loué": ["loue", "louee", "bail en cours"],
    "rez-de-chaussée": ["rez de chaussee", "rdc", "rez de jardin"],
    "sans ascenseur": ["sans ascenseur", "pas d ascenseur", "aucun ascenseur"],
    "sous compromis": ["sous compromis", "sous offre", "offre acceptee"],
    "enchères": ["enchere", "encheres", "immo interactif", "vente aux encheres"],
}

# « travaux », sauf « sans travaux », « aucun travaux », « aucuns travaux », « pas de travaux ».
TRAVAUX_PATTERN = re.compile(r"(?<!sans )(?<!aucun )(?<!aucuns )(?<!pas de )\btravaux\b")

SELLER_TYPES = {"pro": "pro", "private": "particulier"}

# Attributs Leboncoin : on cherche d'abord par clé technique, puis par libellé affiché.
SURFACE_KEYS = ("square", "Surface habitable")
ROOMS_KEYS = ("rooms", "Nombre de pièces")
BEDROOMS_KEYS = ("bedrooms", "Nombre de chambres")
FLOOR_KEYS = ("floor_number", "Étage de votre bien")
SELL_TYPE_KEYS = ("immo_sell_type",)
CONDITION_KEYS = ("global_condition", "État du bien")
ELEVATOR_KEYS = ("elevator", "Ascenseur")


def normalize_text(text: Optional[str]) -> str:
    """« À RÉNOVER ! » devient « a renover » ; entouré d'espaces pour chercher des mots entiers."""
    if not text:
        return " "
    # On retire seulement les accents : l'apostrophe typographique (’) doit devenir un espace,
    # sinon « pas d’ascenseur » deviendrait « pas dascenseur ».
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " " + re.sub(r"[^a-z0-9]+", " ", without_accents.lower()).strip() + " "


def find_text_alerts(title: Optional[str], body: Optional[str]) -> list[str]:
    """Alertes trouvées dans le titre ou le texte complet, dans l'ordre de ALERT_PHRASES."""
    text = normalize_text(f"{title or ''} . {body or ''}")
    found = []
    for alert, phrases in ALERT_PHRASES.items():
        if alert == "travaux":
            hit = TRAVAUX_PATTERN.search(text) is not None
        else:
            hit = any(f" {phrase} " in text for phrase in phrases)
        if hit:
            found.append(alert)
    return found


def _attribute(ad: Any, keys: tuple[str, ...]) -> Optional[str]:
    """Valeur brute d'un attribut, cherché par clé (« square ») ou par libellé (« Surface habitable »)."""
    for attribute in (getattr(ad, "attributes", None) or {}).values():
        if attribute.key in keys or attribute.key_label in keys:
            value = attribute.value if attribute.value not in (None, "") else attribute.value_label
            if value not in (None, ""):
                return str(value)
    return None


def _attribute_says(ad: Any, keys: tuple[str, ...], expected: str) -> bool:
    """Vrai si la valeur ou le libellé affiché de l'attribut vaut expected (sans majuscules ni accents).

    Le libellé compte : pour « Ascenseur », la valeur peut être un code et le libellé « Non ».
    """
    wanted = normalize_text(expected)
    for attribute in (getattr(ad, "attributes", None) or {}).values():
        if attribute.key in keys or attribute.key_label in keys:
            if wanted in (normalize_text(str(attribute.value or "")), normalize_text(str(attribute.value_label or ""))):
                return True
    return False


def _first_int(value: Optional[str]) -> Optional[int]:
    """« 40 m² » → 40, « 1 ch. » → 1, rien → None."""
    match = re.search(r"\d+", value or "")
    return int(match.group()) if match else None


def find_alerts(ad: Any) -> list[str]:
    """Alertes du texte, plus quatre attributs Leboncoin : vente en viager, étage 0,
    état « Travaux à prévoir » et ascenseur « Non »."""
    alerts = set(find_text_alerts(ad.subject, ad.body))
    if _attribute_says(ad, SELL_TYPE_KEYS, "viager"):
        alerts.add("viager")
    if _first_int(_attribute(ad, FLOOR_KEYS)) == 0:
        alerts.add("rez-de-chaussée")
    if _attribute_says(ad, CONDITION_KEYS, "Travaux à prévoir"):
        alerts.add("travaux")
    if _attribute_says(ad, ELEVATOR_KEYS, "Non"):
        alerts.add("sans ascenseur")
    return [alert for alert in ALERT_PHRASES if alert in alerts]  # ordre stable


def district_from_label(city_label: Optional[str]) -> Optional[str]:
    """« Antibes 06600 Coeur de ville » → « Coeur de ville ». Déclaré par le vendeur : souvent faux."""
    match = re.search(r"\b\d{5}\s+(.+)$", city_label or "")
    return match.group(1).strip() if match else None


def seller_type(ad: Any) -> str:
    owner = (getattr(ad, "raw", None) or {}).get("owner") or {}
    return SELLER_TYPES.get(owner.get("type"), "inconnu")


def compact_ad(ad: Any) -> dict:
    """Annonce lbc → dictionnaire court (15 champs)."""
    location = ad.location
    return {
        "id": ad.id,
        "title": ad.subject,
        "price": ad.price,
        "url": ad.url,
        "city": location.city,
        "zipcode": location.zipcode,
        "district": district_from_label(location.city_label),
        "lat": location.lat,
        "lng": location.lng,
        "surface": _first_int(_attribute(ad, SURFACE_KEYS)),
        "rooms": _first_int(_attribute(ad, ROOMS_KEYS)),
        "bedrooms": _first_int(_attribute(ad, BEDROOMS_KEYS)),
        "seller": seller_type(ad),
        "published": ad.first_publication_date,
        "alerts": find_alerts(ad),
    }

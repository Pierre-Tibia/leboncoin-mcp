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
                  "rafraichissement a prevoir", "a restaurer", "a moderniser"],
    "travaux": [],  # cherché à part : voir TRAVAUX_PATTERN (négations exclues)
    "viager": ["viager", "viagere", "nue propriete"],
    "occupé": ["occupe", "occupee", "locataire en place"],
    "loué": ["loue", "louee", "bail en cours"],
    "rez-de-chaussée": ["rez de chaussee", "rdc", "rez de jardin"],
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


def normalize_text(text: Optional[str]) -> str:
    """« À RÉNOVER ! » devient « a renover » ; entouré d'espaces pour chercher des mots entiers."""
    if not text:
        return " "
    without_accents = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
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


def _first_int(value: Optional[str]) -> Optional[int]:
    """« 40 m² » → 40, « 1 ch. » → 1, rien → None."""
    match = re.search(r"\d+", value or "")
    return int(match.group()) if match else None


def find_alerts(ad: Any) -> list[str]:
    """Alertes du texte, plus deux attributs Leboncoin : vente en viager et étage 0."""
    alerts = find_text_alerts(ad.subject, ad.body)
    if (_attribute(ad, SELL_TYPE_KEYS) or "").lower() == "viager" and "viager" not in alerts:
        alerts.append("viager")
    if _first_int(_attribute(ad, FLOOR_KEYS)) == 0 and "rez-de-chaussée" not in alerts:
        alerts.append("rez-de-chaussée")
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

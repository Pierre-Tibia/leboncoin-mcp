"""Tool MCP : comparer le prix au m² d'un bien aux ventes réelles du quartier.

On cherche dans DVF (Demandes de valeurs foncières) les ventes de biens du
même type autour d'un point, on calcule leur prix médian au m², puis l'écart
en pourcentage avec le prix du bien.

Données (Licence Ouverte Etalab v2.0), téléchargées à la demande puis gardées
en cache dans data/dvf_cache/ :
  - DVF géolocalisées, un fichier CSV par commune et par année :
    https://files.data.gouv.fr/geo-dvf/latest/csv/
  - API Découpage administratif, pour savoir quelles communes sont proches :
    https://geo.api.gouv.fr/communes
Internet est nécessaire la première fois qu'on interroge une zone, puis pour
rafraîchir le cache tous les 30 jours (hors ligne, l'ancien cache est utilisé).

DVF ne couvre pas l'Alsace-Moselle (57, 67, 68) ni Mayotte (976).
"""

import csv
import gzip
import io
import json
import math
import re
import ssl
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from typing import NamedTuple

DVF_BASE_URL = "https://files.data.gouv.fr/geo-dvf/latest/csv"
GEO_API_URL = "https://geo.api.gouv.fr/communes"
CACHE_DIR = Path(__file__).resolve().parent / "data" / "dvf_cache"
COMMUNES_CACHE_FILE = CACHE_DIR / "communes.json"
YEARS_CACHE_FILE = CACHE_DIR / "years.json"

# Etalab republie DVF environ deux fois par an (l'année la plus récente se
# complète, une nouvelle année apparaît). Au-delà de cet âge, un fichier du
# cache est retéléchargé ; s'il n'y a pas d'Internet, on garde l'ancien.
CACHE_MAX_AGE_DAYS = 30

EARTH_RADIUS_M = 6_371_000

MIN_SALES = 10                # en dessous : résultat jugé peu fiable
START_RADIUS_M = 500          # rayon de départ
RADIUS_STEP_M = 250           # pas d'élargissement
DEFAULT_MAX_RADIUS_M = 1000
LIMIT_MAX_RADIUS_M = 5000     # au-delà, trop de communes à télécharger
DEFAULT_YEARS = 3
LIMIT_YEARS = 5               # DVF géolocalisées ne remonte qu'à 5 ans

# Prix au m² hors de cette plage = vente « anormale » (vente à 1 €, viager,
# erreur de saisie…). On l'écarte pour ne pas fausser la médiane.
MIN_PRICE_M2 = 500
MAX_PRICE_M2 = 30_000

# Ventes de « surface comparable » : entre 70 % et 130 % de la surface du bien.
# Le prix au m² baisse quand la surface augmente (un studio se vend plus cher
# au m² qu'un 4 pièces) : comparer à toutes les surfaces fausse l'écart.
SURFACE_MIN_RATIO = 0.7
SURFACE_MAX_RATIO = 1.3

PROPERTY_TYPES = {"appartement": "Appartement", "maison": "Maison"}
HOUSING_TYPES = set(PROPERTY_TYPES.values())
OUTBUILDING_TYPE = "Dépendance"  # cave, parking, garage… : autorisés avec le logement

NOT_COVERED_DEPARTMENTS = {"57": "Moselle", "67": "Bas-Rhin", "68": "Haut-Rhin", "976": "Mayotte"}
# Pour ces villes, DVF découpe les fichiers par arrondissement, pas par commune.
CITIES_WITH_ARRONDISSEMENTS = {"75056", "13055", "69123"}  # Paris, Marseille, Lyon

HTTP_TIMEOUT_S = 30
HTTP_RETRIES = 3
USER_AGENT = "leboncoin-mcp"


class Sale(NamedTuple):
    date: str            # AAAA-MM-JJ
    lat: float
    lng: float
    property_type: str   # "Appartement" ou "Maison"
    price_per_m2: float
    surface: float = 0.0  # surface habitable en m²


# ---------------------------------------------------------------- Internet

def _ssl_context() -> ssl.SSLContext:
    """Contexte HTTPS. Sur macOS, le Python de python.org n'a pas toujours les
    certificats racine : on utilise alors ceux de `certifi` s'il est déjà
    installé (c'est le cas ici, via les dépendances de fastmcp)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _http_get(url: str) -> bytes | None:
    """Télécharge une URL. Renvoie None si elle n'existe pas (404).

    Les services publics répondent parfois lentement ou par une erreur 5xx
    passagère : on réessaie quelques fois avant d'abandonner.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(HTTP_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S, context=_ssl_context()) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            if error.code < 500 or attempt == HTTP_RETRIES - 1:
                raise RuntimeError(f"Erreur HTTP {error.code} en interrogeant {url}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == HTTP_RETRIES - 1:
                raise RuntimeError(
                    f"Impossible de joindre {url} ({error}). Une connexion Internet est "
                    "nécessaire la première fois qu'on interroge une zone."
                ) from error
        time.sleep(1 + attempt)
    return None  # jamais atteint


# ---------------------------------------------------------------- Communes

def _load_communes_cache() -> dict:
    if COMMUNES_CACHE_FILE.exists():
        return json.loads(COMMUNES_CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def _save_communes_cache(data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    COMMUNES_CACHE_FILE.write_text(json.dumps(data), encoding="utf-8")


def _commune_at(lat: float, lng: float) -> tuple[str, str] | None:
    """(code commune, code département) du point, ou None s'il est en mer / hors de France."""
    url = f"{GEO_API_URL}?lat={lat}&lon={lng}&fields=code,codeDepartement"
    found = json.loads(_http_get(url) or b"[]")
    if not found:
        return None
    code, department = found[0]["code"], found[0]["codeDepartement"]
    if code in CITIES_WITH_ARRONDISSEMENTS:
        found = json.loads(_http_get(url + "&type=arrondissement-municipal") or b"[]")
        if found:
            code = found[0]["code"]
    return code, department


def _point_at(lat: float, lng: float, distance_m: float, bearing_deg: float) -> tuple[float, float]:
    """Point situé à `distance_m` mètres dans la direction `bearing_deg` (0 = nord)."""
    bearing = math.radians(bearing_deg)
    d_lat = distance_m * math.cos(bearing) / EARTH_RADIUS_M
    d_lng = distance_m * math.sin(bearing) / (EARTH_RADIUS_M * math.cos(math.radians(lat)))
    return lat + math.degrees(d_lat), lng + math.degrees(d_lng)


def _communes_around(lat: float, lng: float, radius_m: float) -> tuple[tuple[str, str] | None, set[tuple[str, str]]]:
    """Commune du point + toutes les communes touchées par le cercle.

    On interroge le centre et 12 points répartis sur le cercle : cela attrape
    les communes voisines quand le bien est près d'une limite communale.
    Les réponses sont gardées en cache sur disque (clé arrondie à ~10 m).
    """
    points = [(lat, lng)] + [_point_at(lat, lng, radius_m, angle) for angle in range(0, 360, 30)]
    keys = [f"{p_lat:.4f},{p_lng:.4f}" for p_lat, p_lng in points]
    cached = _load_communes_cache()
    missing = [(key, point) for key, point in zip(keys, points) if key not in cached]
    if missing:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = pool.map(lambda item: _commune_at(*item[1]), missing)
            for (key, _), result in zip(missing, results):
                cached[key] = result
        _save_communes_cache(cached)
    center = tuple(cached[keys[0]]) if cached[keys[0]] else None
    communes = {tuple(value) for value in (cached[key] for key in keys) if value}
    return center, communes


# ---------------------------------------------------------------- Ventes DVF

def _is_fresh(path: Path) -> bool:
    """Vrai si le fichier du cache a moins de CACHE_MAX_AGE_DAYS jours."""
    age_s = time.time() - path.stat().st_mtime
    return age_s < CACHE_MAX_AGE_DAYS * 24 * 3600


def _available_years() -> tuple[int, ...]:
    """Années publiées dans DVF géolocalisées (ex. 2021…2025).

    La liste est gardée dans data/dvf_cache/years.json et revérifiée en ligne
    quand elle a plus de CACHE_MAX_AGE_DAYS jours : une nouvelle année publiée
    est donc vue sans redémarrer le serveur. Hors ligne, on garde l'ancienne
    liste, ou à défaut les années déjà présentes dans le cache.
    """
    if YEARS_CACHE_FILE.exists() and _is_fresh(YEARS_CACHE_FILE):
        return tuple(json.loads(YEARS_CACHE_FILE.read_text(encoding="utf-8")))
    try:
        listing = (_http_get(f"{DVF_BASE_URL}/") or b"").decode("utf-8", "replace")
        years = sorted({int(y) for y in re.findall(r"/csv/(\d{4})/", listing)})
    except RuntimeError:
        years = []
    if years:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        YEARS_CACHE_FILE.write_text(json.dumps(years), encoding="utf-8")
        return tuple(years)
    # Pas de réponse exploitable : on se rabat sur ce qu'on a déjà.
    if YEARS_CACHE_FILE.exists():
        return tuple(json.loads(YEARS_CACHE_FILE.read_text(encoding="utf-8")))
    years = sorted(int(p.name) for p in CACHE_DIR.glob("[0-9][0-9][0-9][0-9]") if p.is_dir())
    if not years:
        raise RuntimeError("Impossible de connaître les années DVF disponibles (pas d'Internet ni de cache).")
    return tuple(years)


def _extract_sales(rows) -> list[Sale]:
    """Transforme les lignes brutes DVF en ventes exploitables.

    Dans DVF, une vente (« mutation ») occupe plusieurs lignes : une par
    logement, dépendance, parcelle… et le prix TOTAL est répété sur chaque
    ligne. On ne garde donc que les ventes :
      - de type « Vente » (pas d'échange, d'adjudication, de VEFA…) ;
      - qui contiennent UN SEUL logement (appartement ou maison) ;
      - éventuellement accompagné de dépendances (cave, parking, garage) ;
      - sans local commercial (son prix serait mélangé à celui du logement) ;
      - avec une surface habitable connue et un prix au m² plausible.
    """
    mutations: dict[str, list[dict]] = {}
    for row in rows:
        mutations.setdefault(row["id_mutation"], []).append(row)

    sales = []
    for mutation_rows in mutations.values():
        if any(row["nature_mutation"] != "Vente" for row in mutation_rows):
            continue
        types = {row["type_local"] for row in mutation_rows if row["type_local"]}
        if not types <= HOUSING_TYPES | {OUTBUILDING_TYPE}:
            continue  # contient un local commercial ou industriel
        # Un même logement peut apparaître sur plusieurs lignes (une par
        # nature de terrain, par exemple). On les fusionne avant de compter.
        housings = {
            (row["id_parcelle"], row["lot1_numero"], row["type_local"],
             row["surface_reelle_bati"], row["nombre_pieces_principales"]): row
            for row in mutation_rows if row["type_local"] in HOUSING_TYPES
        }
        if len(housings) != 1:
            continue  # aucun logement, ou plusieurs logements vendus ensemble
        housing = next(iter(housings.values()))
        try:
            price = float(housing["valeur_fonciere"])
            surface = float(housing["surface_reelle_bati"])
            lat, lng = float(housing["latitude"]), float(housing["longitude"])
        except ValueError:
            continue  # donnée manquante
        if surface <= 0:
            continue
        price_per_m2 = price / surface
        if not MIN_PRICE_M2 <= price_per_m2 <= MAX_PRICE_M2:
            continue
        sales.append(Sale(housing["date_mutation"], lat, lng, housing["type_local"], price_per_m2, surface))
    return sales


def _commune_sales(code: str, year: int) -> tuple[Sale, ...]:
    """Ventes d'une commune pour une année.

    Lues depuis data/dvf_cache/<année>/<commune>.csv.gz (ou <commune>.none si
    la commune n'a pas de fichier DVF cette année-là). Le fichier est
    (re)téléchargé s'il manque ou a plus de CACHE_MAX_AGE_DAYS jours ; si le
    téléchargement échoue, on garde la version périmée plutôt que d'échouer.
    """
    folder = CACHE_DIR / str(year)
    data_file, absent_file = folder / f"{code}.csv.gz", folder / f"{code}.none"
    cached = next((f for f in (data_file, absent_file) if f.exists()), None)

    if cached is None or not _is_fresh(cached):
        department = code[:3] if code.startswith("97") else code[:2]
        try:
            content = _http_get(f"{DVF_BASE_URL}/{year}/communes/{department}/{code}.csv")
        except RuntimeError:
            if cached is None:
                raise  # ni Internet ni cache : impossible de continuer
        else:
            folder.mkdir(parents=True, exist_ok=True)
            if content is None:
                data_file.unlink(missing_ok=True)
                absent_file.touch()  # touch() remet aussi la date à aujourd'hui
                cached = absent_file
            else:
                data_file.write_bytes(gzip.compress(content))
                absent_file.unlink(missing_ok=True)
                cached = data_file

    if cached == absent_file:
        return ()
    return _read_sales(str(data_file), data_file.stat().st_mtime)


@cache
def _read_sales(path: str, modified_at: float) -> tuple[Sale, ...]:
    """Lit et nettoie un fichier du cache. Gardé en mémoire pour aller vite ;
    la date de modification fait partie de la clé, donc un fichier
    retéléchargé est relu automatiquement."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return tuple(_extract_sales(csv.DictReader(f)))


# ---------------------------------------------------------------- Calcul

def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance à vol d'oiseau en mètres (formule de haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lat, d_lng = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _radii(max_radius_m: int) -> list[int]:
    """Rayons à essayer : 500, 750, 1000… jusqu'à max_radius_m inclus."""
    radii = list(range(START_RADIUS_M, max_radius_m + 1, RADIUS_STEP_M))
    if not radii or radii[-1] != max_radius_m:
        radii.append(max_radius_m)
    return radii


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 février
        return day.replace(year=day.year - years, day=28)


def _median_and_gap(prices: list[float], price_per_m2: float) -> tuple[int | None, float | None]:
    """Médiane arrondie à l'euro et écart en % du bien par rapport à elle."""
    if not prices:
        return None, None
    median = statistics.median(prices)
    return round(median), round((price_per_m2 - median) / median * 100, 1)


def _surface_range(surface_m2: float) -> tuple[float, float]:
    """Plage des surfaces comparables (bornes incluses)."""
    return round(surface_m2 * SURFACE_MIN_RATIO, 2), round(surface_m2 * SURFACE_MAX_RATIO, 2)


def _compare(sales: list[Sale], lat: float, lng: float, price_per_m2: float,
             max_radius_m: int, years: int, surface_m2: float | None = None) -> dict:
    """Cœur du calcul, sans Internet : filtre période, rayon adaptatif, médiane, écart.

    Sans surface_m2, toutes les ventes du type comptent. Avec surface_m2, seules
    les ventes de surface comparable comptent pour le rayon adaptatif et la
    médiane principale ; la médiane toutes surfaces est donnée en plus
    (bloc all_sizes), calculée au même rayon pour comparer à périmètre égal.
    """
    if surface_m2 is None:
        def is_comparable(sale: Sale) -> bool:
            return True
    else:
        min_surface, max_surface = _surface_range(surface_m2)

        def is_comparable(sale: Sale) -> bool:
            return min_surface <= sale.surface <= max_surface

    if not sales:
        result = {
            "median_price_per_m2": None, "gap_percent": None, "radius_m": max_radius_m,
            "sales_count": 0, "reliable": False, "period": None, "latest_sale_date": None,
            "warning": "Aucune vente comparable trouvée dans DVF autour de ce point : comparaison impossible.",
        }
        in_radius = []
    else:
        # La période se compte depuis la dernière vente connue : DVF a environ
        # un an de retard sur l'actualité.
        period_end = max(sale.date for sale in sales)
        period_start = (_years_before(date.fromisoformat(period_end), years) + timedelta(days=1)).isoformat()
        recent = [sale for sale in sales if sale.date >= period_start]
        with_distance = [(_distance_m(lat, lng, s.lat, s.lng), s) for s in recent]

        # Le rayon s'élargit tant qu'il y a moins de MIN_SALES ventes comparables.
        for radius in _radii(max_radius_m):
            in_radius = [sale for distance, sale in with_distance if distance <= radius]
            used = [sale for sale in in_radius if is_comparable(sale)]
            if len(used) >= MIN_SALES:
                break

        median, gap = _median_and_gap([sale.price_per_m2 for sale in used], price_per_m2)
        result = {
            "median_price_per_m2": median,
            "gap_percent": gap,
            "radius_m": radius,
            "sales_count": len(used),
            "reliable": len(used) >= MIN_SALES,
            "period": {"from": period_start, "to": period_end},
            # Date de la vente la plus récente parmi celles réellement utilisées.
            "latest_sale_date": max(sale.date for sale in used) if used else None,
        }
        if not result["reliable"]:
            what = "vente(s) comparable(s)"
            if surface_m2 is not None:
                what = f"vente(s) de surface comparable ({min_surface:g}–{max_surface:g} m²)"
            result["warning"] = (
                f"PEU FIABLE : seulement {len(used)} {what} dans un rayon de "
                f"{radius} m (minimum souhaité : {MIN_SALES}). Essayez un max_radius_m plus grand "
                "ou une période plus longue."
            )

    if surface_m2 is not None:
        all_median, all_gap = _median_and_gap([sale.price_per_m2 for sale in in_radius], price_per_m2)
        result["surface_range_m2"] = {"min": min_surface, "max": max_surface}
        result["all_sizes"] = {
            "median_price_per_m2": all_median,
            "gap_percent": all_gap,
            "sales_count": len(in_radius),
        }
    return result


# ---------------------------------------------------------------- Tool MCP

def price_vs_market(
    lat: float,
    lng: float,
    property_type: str,
    price_per_m2: float,
    max_radius_m: int = DEFAULT_MAX_RADIUS_M,
    years: int = DEFAULT_YEARS,
    surface_m2: float | None = None,
) -> dict:
    """Compare a property's price per m² with real nearby sales (French DVF open data).

    IMPORTANT: always pass surface_m2 when the property's living area is known.
    Price per m² drops as surface grows (studios sell for more per m² than
    large flats), so without surface_m2 the property is compared with sales of
    all sizes and the gap is biased: large properties look cheaper than the
    market, small ones look more expensive.

    Looks for sales of the same property type around the point, starting at a
    500 m radius and widening by 250 m steps up to max_radius_m until at least
    10 sales are found. With surface_m2, only sales between 70 % and 130 % of
    that surface count (for the radius, the median and the gap), and the
    median over all sizes is returned too, in all_sizes, for comparison. Returns the median price per m² of those sales and the
    gap in percent (positive = the property is more expensive than the market).
    Works everywhere in France except Alsace-Moselle (57, 67, 68) and Mayotte,
    which are not covered by DVF. The period is counted back from the most
    recent sale available in DVF (the data lags about one year behind).

    Args:
        lat: Latitude in decimal degrees (e.g. 43.58).
        lng: Longitude in decimal degrees (e.g. 7.125).
        property_type: "appartement" or "maison".
        price_per_m2: Asking price per m² of the property, in euros.
        max_radius_m: Maximum search radius in meters (default 1000).
        years: Number of years of sales to use (default 3, max 5).
        surface_m2: Living area of the property in m². Strongly recommended
            (see above); omit it only if the surface is unknown.

    Returns:
        median_price_per_m2, gap_percent, radius_m (radius finally used),
        sales_count, reliable (False if fewer than 10 sales), period {from, to},
        latest_sale_date (most recent sale among those used),
        warning (only when the result is not reliable), property_type, source.
        With surface_m2, the first fields describe comparable-size sales only,
        plus surface_range_m2 {min, max} and all_sizes {median_price_per_m2,
        gap_percent, sales_count} computed over all sizes at the same radius.
    """
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("Coordonnées invalides : lat doit être entre -90 et 90, lng entre -180 et 180.")
    dvf_type = PROPERTY_TYPES.get(property_type.strip().lower())
    if dvf_type is None:
        raise ValueError('property_type doit valoir "appartement" ou "maison".')
    if price_per_m2 <= 0:
        raise ValueError("price_per_m2 doit être strictement positif.")
    if not 0 < max_radius_m <= LIMIT_MAX_RADIUS_M:
        raise ValueError(f"max_radius_m doit être entre 1 et {LIMIT_MAX_RADIUS_M} mètres.")
    if not 1 <= years <= LIMIT_YEARS:
        raise ValueError(f"years doit être entre 1 et {LIMIT_YEARS}.")
    if surface_m2 is not None and surface_m2 <= 0:
        raise ValueError("surface_m2 doit être strictement positif.")

    center, communes = _communes_around(lat, lng, max_radius_m)
    if center is None and not communes:
        raise ValueError("Aucune commune française trouvée autour de ce point (en mer ou hors de France ?).")
    if center and center[1] in NOT_COVERED_DEPARTMENTS:
        raise ValueError(
            f"Ce point est en {NOT_COVERED_DEPARTMENTS[center[1]]} : ce territoire n'est pas couvert par DVF "
            "(il relève du livre foncier)."
        )

    # On charge une année de plus que demandé : la période se compte depuis la
    # dernière vente, qui peut tomber en fin d'année.
    available = _available_years()
    wanted_years = [y for y in available if y >= available[-1] - years]
    sales = [
        sale
        for code, department in sorted(communes)
        if department not in NOT_COVERED_DEPARTMENTS
        for year in wanted_years
        for sale in _commune_sales(code, year)
        if sale.property_type == dvf_type
    ]

    result = _compare(sales, lat, lng, price_per_m2, max_radius_m, years, surface_m2)
    result["property_type"] = property_type.strip().lower()
    result["source"] = "DVF géolocalisées (Etalab / DGFiP), data.gouv.fr"
    return result

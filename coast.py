"""Tool MCP : temps de marche estimé jusqu'à la côte la plus proche.

Version 1 : distance à vol d'oiseau jusqu'au trait de côte, convertie en
minutes de marche. Couvre la France métropolitaine et la Corse.

Données : data/coastline_fr.json.gz, produit par scripts/build_coastline.py
à partir du trait de côte HISTOLITT® (SHOM / IGN, Licence Ouverte Etalab v2.0).
"""

import gzip
import json
import math
from functools import cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent / "data" / "coastline_fr.json.gz"

EARTH_RADIUS_M = 6_371_000
DEFAULT_WALKING_SPEED_KMH = 5.0

# Zone couverte par les données (métropole + Corse, avec une marge).
FRANCE_BBOX = {"lat_min": 41.0, "lat_max": 51.5, "lng_min": -5.5, "lng_max": 10.0}

# Taille d'une case de la grille de recherche, en degrés (~10 km).
CELL_DEG = 0.1
# Plus petite largeur d'une case en mètres sur la zone couverte (au nord,
# les degrés de longitude sont les plus courts). Sert à savoir quand arrêter
# la recherche.
_MIN_CELL_M = math.radians(CELL_DEG) * EARTH_RADIUS_M * math.cos(math.radians(FRANCE_BBOX["lat_max"]))


def _cell(lng: float, lat: float) -> tuple[int, int]:
    return math.floor(lng / CELL_DEG), math.floor(lat / CELL_DEG)


@cache
def _load_grid() -> dict[tuple[int, int], list[tuple[float, float, float, float]]]:
    """Charge le littoral et range chaque segment dans les cases de la grille qu'il touche.

    La grille évite de comparer le point à chacun des 200 000 segments :
    on ne regarde que les cases proches. Chargée une seule fois (@cache).
    """
    with gzip.open(DATA_FILE, "rt", encoding="utf-8") as f:
        lines = json.load(f)
    grid: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
    for line in lines:
        for (lng1, lat1), (lng2, lat2) in zip(line, line[1:]):
            segment = (lng1, lat1, lng2, lat2)
            cx1, cy1 = _cell(min(lng1, lng2), min(lat1, lat2))
            cx2, cy2 = _cell(max(lng1, lng2), max(lat1, lat2))
            for cx in range(cx1, cx2 + 1):
                for cy in range(cy1, cy2 + 1):
                    grid.setdefault((cx, cy), []).append(segment)
    return grid


def _distance_to_segment(lat: float, lng: float, segment: tuple[float, float, float, float]):
    """Distance en mètres entre le point et un segment de côte, plus le point le plus proche.

    On « aplatit » localement la Terre autour du point (projection
    équirectangulaire) : très précis à l'échelle de quelques centaines de km.
    """
    lng1, lat1, lng2, lat2 = segment
    k_lat = math.radians(1) * EARTH_RADIUS_M
    k_lng = k_lat * math.cos(math.radians(lat))
    # Coordonnées en mètres, avec le point recherché à l'origine (0, 0).
    ax, ay = (lng1 - lng) * k_lng, (lat1 - lat) * k_lat
    bx, by = (lng2 - lng) * k_lng, (lat2 - lat) * k_lat
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    # t = position du point le plus proche sur le segment (0 = début, 1 = fin).
    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length_sq))
    px, py = ax + t * dx, ay + t * dy
    return math.hypot(px, py), (lat + py / k_lat, lng + px / k_lng)


def walking_time_to_coast(
    lat: float,
    lng: float,
    walking_speed_kmh: float = DEFAULT_WALKING_SPEED_KMH,
) -> dict:
    """Estimate the walking time in minutes from a point to the nearest sea coast.

    Covers mainland France and Corsica. Uses straight-line ("as the crow flies")
    distance to the coastline, so the real walking time is always longer.
    Lakes are not counted as coast.

    Args:
        lat: Latitude in decimal degrees (e.g. 43.5853).
        lng: Longitude in decimal degrees (e.g. 7.1275).
        walking_speed_kmh: Walking speed in km/h (default 5).

    Returns:
        walking_minutes, distance_km, nearest_coast_point {lat, lng}, method.
    """
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("Coordonnées invalides : lat doit être entre -90 et 90, lng entre -180 et 180.")
    if not (FRANCE_BBOX["lat_min"] <= lat <= FRANCE_BBOX["lat_max"]
            and FRANCE_BBOX["lng_min"] <= lng <= FRANCE_BBOX["lng_max"]):
        raise ValueError("Ce point est hors de la zone couverte (France métropolitaine et Corse).")
    if walking_speed_kmh <= 0:
        raise ValueError("walking_speed_kmh doit être strictement positif.")

    grid = _load_grid()
    center_x, center_y = _cell(lng, lat)
    best_dist, best_point = math.inf, None
    ring = 0
    # On explore des « anneaux » de cases de plus en plus larges autour du point.
    # Dès que l'anneau suivant est forcément plus loin que la meilleure distance
    # trouvée, on peut s'arrêter : rien de plus proche n'existe.
    while (ring - 1) * _MIN_CELL_M <= best_dist:
        for cx in range(center_x - ring, center_x + ring + 1):
            for cy in range(center_y - ring, center_y + ring + 1):
                if max(abs(cx - center_x), abs(cy - center_y)) != ring:
                    continue  # case déjà vue dans un anneau précédent
                for segment in grid.get((cx, cy), ()):
                    dist, point = _distance_to_segment(lat, lng, segment)
                    if dist < best_dist:
                        best_dist, best_point = dist, point
        ring += 1

    distance_km = best_dist / 1000
    return {
        "walking_minutes": round(distance_km / walking_speed_kmh * 60),
        "distance_km": round(distance_km, 2),
        "nearest_coast_point": {"lat": round(best_point[0], 5), "lng": round(best_point[1], 5)},
        "method": f"vol d'oiseau, {walking_speed_kmh:g} km/h",
    }

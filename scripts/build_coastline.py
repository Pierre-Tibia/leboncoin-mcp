"""Prépare le fichier de littoral utilisé par le tool `walking_time_to_coast`.

À lancer UNE SEULE FOIS (ou pour mettre à jour la donnée). Uniquement la
bibliothèque standard de Python, aucune dépendance à installer.

Source : trait de côte HISTOLITT® Métropole + Corse (SHOM / IGN),
Licence Ouverte Etalab v2.0.
  https://www.data.gouv.fr/datasets/trait-de-cote-histolitt

Utilisation :
  1. Télécharger et décompresser (ex. avec `bsdtar -xf` sur macOS) :
     https://services.data.shom.fr/INSPIRE/telechargement/prepackageGroup/TCH-PACK_DL/prepackage/TCH_FRA_V2/file/TCH_FRA_V2.7z
  2. python scripts/build_coastline.py <chemin>/TCH_FRA_V2/Shapefile/TCH.shp

Résultat : data/coastline_fr.json.gz, une liste de lignes de côte,
chacune étant une liste de points [longitude, latitude] (WGS84).
"""

import gzip
import json
import math
import struct
import sys
from pathlib import Path

# Tolérance de simplification en mètres : on supprime les points qui
# s'écartent de moins de 10 m de la ligne. Largement suffisant pour
# estimer un temps de marche, et le fichier devient beaucoup plus petit.
SIMPLIFY_TOLERANCE_M = 10.0

OUTPUT = Path(__file__).resolve().parent.parent / "data" / "coastline_fr.json.gz"


def read_polylines(shp_path: str) -> list[list[tuple[float, float]]]:
    """Lit un shapefile de type « polyligne » (type 3) sans bibliothèque externe."""
    data = Path(shp_path).read_bytes()
    lines = []
    offset = 100  # l'en-tête du fichier fait 100 octets
    while offset < len(data):
        _, content_len = struct.unpack(">ii", data[offset:offset + 8])
        record = data[offset + 8:offset + 8 + content_len * 2]
        offset += 8 + content_len * 2
        shape_type = struct.unpack("<i", record[0:4])[0]
        if shape_type != 3:
            continue
        num_parts, num_points = struct.unpack("<ii", record[36:44])
        parts = list(struct.unpack(f"<{num_parts}i", record[44:44 + 4 * num_parts]))
        points_start = 44 + 4 * num_parts
        coords = struct.unpack(f"<{2 * num_points}d",
                               record[points_start:points_start + 16 * num_points])
        points = list(zip(coords[0::2], coords[1::2]))
        for start, end in zip(parts, parts[1:] + [num_points]):
            lines.append(points[start:end])
    return lines


def simplify(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Algorithme de Douglas-Peucker (version itérative)."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        (x1, y1), (x2, y2) = points[first], points[last]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        max_dist, max_index = 0.0, first
        for i in range(first + 1, last):
            px, py = points[i]
            if length == 0:
                dist = math.hypot(px - x1, py - y1)
            else:
                dist = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / length
            if dist > max_dist:
                max_dist, max_index = dist, i
        if max_dist > tolerance:
            keep[max_index] = True
            stack.append((first, max_index))
            stack.append((max_index, last))
    return [p for p, k in zip(points, keep) if k]


# Constantes officielles IGN de la projection Lambert-93 (ellipsoïde GRS80).
_N = 0.7256077650
_C = 11754255.426
_XS = 700000.0
_YS = 12655612.050
_E = 0.0818191910428158
_LON0 = math.radians(3.0)


def lambert93_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """Convertit des coordonnées Lambert-93 (mètres) en (longitude, latitude) en degrés."""
    dx, dy = x - _XS, _YS - y
    r = math.hypot(dx, dy)
    gamma = math.atan2(dx, dy)
    lon = _LON0 + gamma / _N
    iso_lat = -math.log(r / _C) / _N
    lat = 2 * math.atan(math.exp(iso_lat)) - math.pi / 2
    for _ in range(10):  # converge en 3-4 itérations
        es = _E * math.sin(lat)
        lat = 2 * math.atan(((1 + es) / (1 - es)) ** (_E / 2) * math.exp(iso_lat)) - math.pi / 2
    return math.degrees(lon), math.degrees(lat)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    lines = read_polylines(sys.argv[1])
    before = sum(len(line) for line in lines)
    output = []
    for line in lines:
        simplified = simplify(line, SIMPLIFY_TOLERANCE_M)
        if len(simplified) < 2:
            continue
        # 5 décimales ≈ 1 mètre de précision
        output.append([[round(v, 5) for v in lambert93_to_wgs84(x, y)] for x, y in simplified])
    after = sum(len(line) for line in output)
    OUTPUT.parent.mkdir(exist_ok=True)
    with gzip.open(OUTPUT, "wt", encoding="utf-8") as f:
        json.dump(output, f, separators=(",", ":"))
    print(f"{len(output)} lignes, {before} -> {after} points, "
          f"{OUTPUT.stat().st_size / 1e6:.1f} Mo écrits dans {OUTPUT}")


if __name__ == "__main__":
    main()

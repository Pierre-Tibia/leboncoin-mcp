"""Tests du tool walking_time_to_coast.

Lancer depuis la racine du projet :  python -m unittest discover tests
"""

import unittest

from coast import walking_time_to_coast


class WalkingTimeToCoastTest(unittest.TestCase):
    def test_port_vauban_antibes_is_under_5_minutes(self):
        result = walking_time_to_coast(43.5853, 7.1275)
        self.assertLess(result["walking_minutes"], 5)

    def test_grasse_center_is_over_2_hours(self):
        result = walking_time_to_coast(43.6589, 6.9236)
        self.assertGreater(result["walking_minutes"], 120)

    def test_works_elsewhere_in_france(self):
        # Ajaccio (Corse), sur le port
        self.assertLess(walking_time_to_coast(41.9192, 8.7386)["walking_minutes"], 10)
        # Paris : le trait de côte le plus proche est dans l'estuaire de la Seine (~105 km)
        self.assertGreater(walking_time_to_coast(48.8566, 2.3522)["distance_km"], 100)

    def test_slower_walker_takes_longer(self):
        normal = walking_time_to_coast(43.6589, 6.9236)
        slow = walking_time_to_coast(43.6589, 6.9236, walking_speed_kmh=2.5)
        self.assertAlmostEqual(slow["walking_minutes"], 2 * normal["walking_minutes"], delta=1)

    def test_rejects_points_outside_france(self):
        with self.assertRaises(ValueError):
            walking_time_to_coast(40.4168, -3.7038)  # Madrid

    def test_rejects_invalid_coordinates(self):
        with self.assertRaises(ValueError):
            walking_time_to_coast(123.0, 7.0)

    def test_rejects_non_positive_speed(self):
        with self.assertRaises(ValueError):
            walking_time_to_coast(43.5853, 7.1275, walking_speed_kmh=0)


if __name__ == "__main__":
    unittest.main()

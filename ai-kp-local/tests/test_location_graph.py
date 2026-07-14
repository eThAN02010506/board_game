import unittest

from ai_kp.maps.location_graph import LocationGraph


class LocationGraphTests(unittest.TestCase):
    def test_shortest_route_for_split_party_planning(self) -> None:
        graph = LocationGraph()
        graph.add_route("旅馆", "旧码头")
        graph.add_route("旧码头", "报社")
        graph.add_route("旅馆", "警局")

        self.assertEqual(graph.shortest_route("旅馆", "报社"), ["旅馆", "旧码头", "报社"])


if __name__ == "__main__":
    unittest.main()


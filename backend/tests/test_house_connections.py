"""Run directly with Python to verify the house topology without test cleanup."""
import copy
import unittest

from app.house_scan import load_house_package
from pipeline.house_connectivity import build_house_connections, solve_door_transforms
from pipeline.house_demo import PORTALS, ROOMS


def source_manifest():
    return {"synthetic": True, "unit": "m", "up_axis": "z",
            "rooms": copy.deepcopy(list(ROOMS)), "portals": copy.deepcopy(list(PORTALS))}


class HouseConnectionsTest(unittest.TestCase):
    def test_all_rooms_connect_through_paired_door_faces(self):
        graph = build_house_connections(source_manifest())
        self.assertEqual(len(graph["rooms"]), 5)
        self.assertEqual(len(graph["connections"]), 4)
        self.assertEqual({link["rooms"][0] for link in graph["connections"]},
                         {"living_room", "bedroom", "kitchen", "bathroom"})
        for link in graph["connections"]:
            self.assertEqual(len(link["doors"]), 2)
            self.assertNotEqual(link["doors"][0]["side"], link["doors"][1]["side"])
            self.assertEqual(link["width_m"], 0.9)

    def test_rejects_mismatched_or_disconnected_doors(self):
        manifest = source_manifest()
        manifest["portals"][0]["plane"] += 0.2
        with self.assertRaisesRegex(ValueError, "does not touch"):
            build_house_connections(manifest)
        manifest = source_manifest()
        manifest["portals"].pop()
        with self.assertRaisesRegex(ValueError, "does not connect"):
            build_house_connections(manifest)

    def test_paired_door_aligns_a_rotated_room(self):
        rooms = [{"id": "a", "origin": [0, 0, 0]}, {"id": "b", "origin": [0, 0, 0]}]
        connections = [{"id": "D01", "doors": [
            {"room_id": "a", "local_center": [4, 1, 1], "normal": [1, 0]},
            {"room_id": "b", "local_center": [0, 2, 1], "normal": [0, -1]},
        ]}]
        poses = solve_door_transforms(rooms, connections)
        self.assertEqual(poses["b"], {"rotation_z_deg": 270, "translation_m": [2, 1, 0]})

    def test_packaged_pointcloud_and_framework_match(self):
        graph, structure = load_house_package()
        self.assertEqual(len(graph["connections"]), len(structure["doors"]))
        self.assertGreater(len(structure["walls"]), 40)
        self.assertGreater(len(structure["semantic_instances"]), 30)
        self.assertEqual({item["instance_id"] for item in structure["doors"]},
                         {item["id"] for item in graph["connections"]})


if __name__ == "__main__":
    unittest.main()

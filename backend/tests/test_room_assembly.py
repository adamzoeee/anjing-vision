"""Project room scans can be linked by doors without changing their sources."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import open3d as o3d
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.db import Base, get_db
from app.main import create_app
from app.models import Scan
from app.room_assembly import solve_assembly


class RoomAssemblyTest(unittest.TestCase):
    def test_multi_room_graph_and_unconnected_room(self):
        def room(scan_id, doors):
            return {"id": str(scan_id), "scan_id": scan_id, "label": f"房间 #{scan_id}",
                "floor": [[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0]],
                "structure": {"room": {"height_m": 2.5}}, "doors": doors}

        def door(name, center, normal):
            return {"id": name, "center": center, "normal": normal, "width_m": .8, "height_m": 2}

        rooms = [room(1, [door("east", [2, 1, 1], [1, 0])]),
                 room(2, [door("north", [1, 2, 1], [0, 1]), door("east", [2, 1, 1], [1, 0])]),
                 room(3, [door("west", [0, 1, 1], [-1, 0])])]
        links = [{"scan_a": 1, "door_a": "east", "scan_b": 2, "door_b": "north"},
                 {"scan_a": 2, "door_a": "east", "scan_b": 3, "door_b": "west"}]
        graph = solve_assembly(rooms, links)
        self.assertEqual(len(graph["rooms"]), 3)
        self.assertAlmostEqual(graph["transforms"]["2"]["rotation_z_deg"] % 360, 90)
        self.assertEqual(len(graph["connections"]), 2)
        with self.assertRaisesRegex(ValueError, "未覆盖"):
            solve_assembly(rooms, links[:1])

    def test_project_rooms_become_one_new_scan(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        output = Path(tempfile.mkdtemp(prefix="anjing-room-assembly-"))
        settings = Settings(auto_create_tables=False, data_dir=str(output))
        app = create_app(settings)

        def database():
            with sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_settings] = lambda: settings
        with TestClient(app) as client:
            def register(name, email):
                response = client.post("/api/auth/register", json={"org_name": name, "name": "Tester",
                    "email": email, "password": "secret123"})
                self.assertEqual(response.status_code, 200, response.text)
                return {"Authorization": f"Bearer {response.json()['token']}"}

            owner = register("Assembly Org A", "assembly-a@example.com")
            stranger = register("Assembly Org B", "assembly-b@example.com")
            project = client.post("/api/projects", json={"name": "逐房间扫描"}, headers=owner).json()
            room_ids = []
            for center_x in (2.0, 0.0):
                created = client.post(f"/api/projects/{project['id']}/scans", json={"capture_type": "video"}, headers=owner)
                self.assertEqual(created.status_code, 200, created.text)
                room_id = created.json()["id"]
                room_ids.append(room_id)
                with sessions() as db:
                    scan = db.get(Scan, room_id)
                    scan.status = "done"
                    scan.progress = 100
                    db.commit()
                post = output / "work" / str(room_id) / "postprocess"
                post.mkdir(parents=True)
                structure = {"schema_version": 1, "coordinate_unit": "meters", "z_up": True,
                    "room": {"height_m": 2.5, "floor_polygon": [[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0]]},
                    "walls": [{"center": [1, 0, 1.25], "size": [2, .06, 2.5], "rotation_z_deg": 0}],
                    "doors": [{"center": [center_x, 1, 1], "size": [.8, .1, 2], "rotation_z_deg": 90}],
                    "windows": [], "semantic_instances": []}
                (post / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
                cloud = o3d.geometry.PointCloud()
                cloud.points = o3d.utility.Vector3dVector(np.array([[0, 0, 0], [2, 0, 0], [1, 1, 1]], dtype=float))
                cloud.colors = o3d.utility.Vector3dVector(np.array([[1, 0, 0]] * 3, dtype=float))
                self.assertTrue(o3d.io.write_point_cloud(str(post / "scene_preview.ply"), cloud))

            route = f"/api/projects/{project['id']}/scans/house"
            self.assertEqual(client.get(f"{route}/rooms", headers=stranger).status_code, 404)
            catalog = client.get(f"{route}/rooms", headers=owner)
            self.assertEqual(catalog.status_code, 200, catalog.text)
            self.assertEqual(len(catalog.json()["rooms"]), 2)
            self.assertTrue(all(room["ready"] for room in catalog.json()["rooms"]))
            payload = {"scan_ids": room_ids, "labels": {str(room_ids[0]): "客厅", str(room_ids[1]): "卧室"},
                "links": [{"scan_a": room_ids[0], "door_a": "door_01",
                "scan_b": room_ids[1], "door_b": "door_01"}]}
            self.assertEqual(client.post(route, json=payload, headers=stranger).status_code, 404)
            invalid = json.loads(json.dumps(payload))
            invalid["links"][0]["scan_b"] = room_ids[0]
            self.assertEqual(client.post(route, json=invalid, headers=owner).status_code, 422)
            created = client.post(route, json=payload, headers=owner)
            self.assertEqual(created.status_code, 200, created.text)
            house = created.json()
            self.assertEqual((house["capture_type"], house["status"]), ("house", "done"))
            graph = client.get(f"/api/preview/{house['id']}/house-connections.json", headers=owner).json()
            self.assertEqual(len(graph["rooms"]), 2)
            self.assertEqual([room["label"] for room in graph["rooms"]], ["客厅", "卧室"])
            self.assertEqual(len(graph["connections"]), 1)
            self.assertAlmostEqual(graph["transforms"][str(room_ids[1])]["translation_m"][0], 2.0)
            structure = client.get(f"/api/preview/{house['id']}/structure.json", headers=owner).json()
            self.assertEqual(len(structure["walls"]), 2)
            self.assertEqual([door["instance_id"] for door in structure["doors"]], ["D01", "D01"])
            cloud = o3d.io.read_point_cloud(str(output / "work" / str(house["id"]) / "postprocess" / "scene_preview.ply"))
            self.assertEqual(len(cloud.points), 6)
            self.assertEqual(client.post(f"/api/scans/{house['id']}/upload", headers=owner,
                                         files=[("files", ("x.mp4", b"x", "video/mp4"))]).status_code, 409)
            with sessions() as db:
                self.assertTrue(all(db.get(Scan, room_id).status == "done" for room_id in room_ids))


if __name__ == "__main__":
    unittest.main()

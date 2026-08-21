"""仅为网页显示补齐已确认墙面/地面；绝不修改或参与测量点云。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree


DTYPE = np.dtype([(name, "<f4") for name in ("x", "y", "z", "red", "green", "blue")])


def read_float_ply(path: Path) -> tuple[np.ndarray, bytes]:
    raw = path.read_bytes()
    marker = b"end_header\n"
    end = raw.index(marker) + len(marker)
    header = raw[:end]
    count = int(next(line.split()[2] for line in header.decode("ascii").splitlines() if line.startswith("element vertex")))
    rows = np.frombuffer(raw, dtype=DTYPE, count=count, offset=end).copy()
    return rows, header


def write_float_ply(path: Path, rows: np.ndarray) -> None:
    header = (
        "ply\nformat binary_little_endian 1.0\ncomment Display-only structural plane completion\n"
        f"element vertex {len(rows)}\nproperty float x\nproperty float y\nproperty float z\n"
        "property float red\nproperty float green\nproperty float blue\nend_header\n"
    ).encode("ascii")
    path.write_bytes(header + rows.astype(DTYPE, copy=False).tobytes())


def inside_opening(point: np.ndarray, opening: dict, margin: float = 0.08) -> bool:
    center = np.asarray(opening.get("center", [999, 999, 999]), dtype=float)
    size = np.asarray(opening.get("size", [0, 0, 0]), dtype=float)
    wall_id = int(opening.get("wall_id", -1))
    along = 1 if wall_id in (1, 3) else 0
    return abs(point[along] - center[along]) <= size[0] / 2 + margin and (
        center[2] - size[2] / 2 - margin <= point[2] <= center[2] + size[2] / 2 + margin
    )


def _video_colors(
    points: np.ndarray,
    fallback: np.ndarray,
    cameras_json: Path | None,
    images_dir: Path | None,
    *,
    max_views: int = 60,
) -> tuple[np.ndarray, int]:
    """用整段视频均匀视角给补点着色；三视角中值抑制单帧遮挡/曝光。"""
    if not cameras_json or not images_dir or not cameras_json.is_file() or not images_dir.is_dir():
        return fallback, 0
    import cv2

    cameras = json.loads(cameras_json.read_text(encoding="utf-8"))
    if not cameras:
        return fallback, 0
    indices = np.linspace(0, len(cameras) - 1, min(max_views, len(cameras)), dtype=int)
    best_scores = np.full((len(points), 3), np.inf, dtype=np.float32)
    best_colors = np.repeat(fallback[:, None, :], 3, axis=1).astype(np.float32)
    observations = np.zeros(len(points), dtype=np.uint8)
    used = 0
    for index in sorted(set(indices.tolist())):
        camera = cameras[index]
        view_id = int(camera.get("id", index))
        image = cv2.imread(str(images_dir / f"{view_id:05d}.jpg"), cv2.IMREAD_COLOR)
        if image is None:
            continue
        used += 1
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        rotation = np.asarray(camera["rotation"], dtype=float)
        position = np.asarray(camera["position"], dtype=float)
        cam = (rotation @ (points - position).T).T
        z = cam[:, 2]
        valid = z > 0.05
        u = np.rint(float(camera["fx"]) * cam[:, 0] / np.maximum(z, 1e-6) + float(camera["cx"])).astype(int)
        v = np.rint(float(camera["fy"]) * cam[:, 1] / np.maximum(z, 1e-6) + float(camera["cy"])).astype(int)
        h, w = image.shape[:2]
        valid &= (u >= 1) & (u < w - 1) & (v >= 1) & (v < h - 1)
        ids = np.flatnonzero(valid)
        if not len(ids):
            continue
        # 画面中央、距离较近的视角优先；每点保留三个独立视角，最后取中值。
        radial = ((u[ids] - w / 2) / max(w, 1)) ** 2 + ((v[ids] - h / 2) / max(h, 1)) ** 2
        score = radial.astype(np.float32) + 0.015 * z[ids].astype(np.float32)
        slot = np.argmax(best_scores[ids], axis=1)
        worst = best_scores[ids, slot]
        improve = score < worst
        if not np.any(improve):
            continue
        chosen = ids[improve]
        chosen_slot = slot[improve]
        best_scores[chosen, chosen_slot] = score[improve]
        best_colors[chosen, chosen_slot] = image[v[chosen], u[chosen]]
        observations[chosen] = np.minimum(3, observations[chosen] + 1)
    colors = fallback.copy()
    observed = observations > 0
    if np.any(observed):
        colors[observed] = np.median(best_colors[observed], axis=1)
    return colors, used


def complete(
    input_ply: Path,
    structure_json: Path,
    output_ply: Path,
    *,
    cell: float = 0.015,
    cameras_json: Path | None = None,
    images_dir: Path | None = None,
    max_video_views: int = 60,
) -> dict:
    rows, _ = read_float_ply(input_ply)
    xyz = np.column_stack([rows[name] for name in ("x", "y", "z")]).astype(np.float64)
    rgb = np.column_stack([rows[name] for name in ("red", "green", "blue")]).astype(np.float32)
    structure = json.loads(structure_json.read_text(encoding="utf-8"))
    openings = list(structure.get("doors", [])) + list(structure.get("windows", []))
    room = structure.get("room", {})
    room_height = float(room.get("height_m") or 2.6)
    # 展示副本直接移除原始天花板点；仅停止“补天花板”还不够，原点云中的
    # 顶面仍会像一层星点盖板遮住房间。
    display_keep = xyz[:, 2] < room_height - 0.12
    display_rows = rows[display_keep].copy()
    display_xyz = np.column_stack([display_rows[name] for name in ("x", "y", "z")]).astype(np.float64)
    floor_snap = np.abs(display_xyz[:, 2]) <= 0.075
    display_xyz[floor_snap, 2] = 0.0
    # Never move observed wall points.  Earlier normal snapping moved valid
    # door/window edges onto an imperfect model plane and visibly increased
    # wall misalignment.  Completion may add display-only samples behind an
    # observation, but the original observation remains byte-for-byte located.
    wall_snap_count = 0
    for index, name in enumerate(("x", "y", "z")):
        display_rows[name] = display_xyz[:, index]
    additions: list[tuple[np.ndarray, np.ndarray]] = []
    wall_stats = []
    for wall in structure.get("walls", []):
        wall_id = int(wall.get("id", -1))
        center = np.asarray(wall["center"], dtype=float)
        length, _, height = map(float, wall["size"])
        theta = np.deg2rad(float(wall.get("rotation_z_deg", 0.0)))
        tangent = np.asarray([np.cos(theta), np.sin(theta), 0.0])
        normal = np.asarray([-np.sin(theta), np.cos(theta), 0.0])
        delta = xyz - center
        along = delta @ tangent
        distance = np.abs(delta @ normal)
        near = (distance <= 0.075) & (np.abs(along) <= length / 2 + 0.05) & (xyz[:, 2] >= 0) & (xyz[:, 2] <= height)
        wall_points = xyz[near]
        if len(wall_points) < 100:
            continue
        wall_along = (wall_points - center) @ tangent
        occupied = set(zip(np.rint(wall_along / cell).astype(int), np.rint(wall_points[:, 2] / cell).astype(int)))
        us = np.arange(-length / 2 + cell / 2, length / 2, cell)
        zs = np.arange(cell / 2, height, cell)
        generated = []
        window_flags = []
        for u in us:
            for z in zs:
                key = (int(round(u / cell)), int(round(z / cell)))
                if key in occupied:
                    continue
                point = center + tangent * u
                point[2] = z
                matching = [
                    item for item in openings
                    if int(item.get("wall_id", -2)) == wall_id and inside_opening(point, item)
                ]
                if any(str(item.get("kind")) == "door" for item in matching):
                    continue
                generated.append(point)
                window_flags.append(any(str(item.get("kind")) == "window" for item in matching))
        if not generated:
            continue
        generated_xyz = np.asarray(generated, dtype=np.float64)
        tree = cKDTree(np.column_stack([wall_along, wall_points[:, 2]]))
        _, indices = tree.query(np.column_stack([(generated_xyz - center) @ tangent, generated_xyz[:, 2]]), k=1)
        generated_rgb = rgb[near][indices]
        generated_rgb, video_views = _video_colors(
            generated_xyz, generated_rgb, cameras_json, images_dir, max_views=max_video_views,
        )
        if any(window_flags):
            mask = np.asarray(window_flags, dtype=bool)
            # 玻璃通常没有深度，网页端用低饱和蓝灰色点明确表示窗面，避免
            # 将“无深度”渲染成大片黑洞。这里只影响显示副本。
            generated_rgb[mask] = generated_rgb[mask] * 0.35 + np.asarray([0.28, 0.42, 0.52], dtype=np.float32) * 0.65
        additions.append((generated_xyz, generated_rgb))
        wall_stats.append({
            "wall_id": wall_id, "source_points": int(near.sum()), "filled_points": len(generated),
            "window_display_points": int(sum(window_flags)), "video_color_views": video_views,
        })
    bounds = room.get("bounds_xy", {})
    if isinstance(bounds.get("min"), list) and isinstance(bounds.get("max"), list):
        lo, hi = np.asarray(bounds["min"], dtype=float), np.asarray(bounds["max"], dtype=float)
        xs = np.arange(lo[0] + cell / 2, hi[0], cell)
        ys = np.arange(lo[1] + cell / 2, hi[1], cell)
        # 天花板不补：俯视/旋转时会遮挡整个房间。只补地面和墙。
        for name, z, near_mask in (("floor", 0.0, xyz[:, 2] <= 0.075),):
            support = xyz[near_mask]
            if len(support) < 100:
                continue
            occupied = set(zip(np.rint(support[:, 0] / cell).astype(int), np.rint(support[:, 1] / cell).astype(int)))
            generated = np.asarray([
                [x, y, z] for x in xs for y in ys
                if (int(round(x / cell)), int(round(y / cell))) not in occupied
            ], dtype=np.float64)
            if not len(generated):
                continue
            tree = cKDTree(support[:, :2])
            _, indices = tree.query(generated[:, :2], k=1)
            generated_rgb, video_views = _video_colors(
                generated, rgb[near_mask][indices], cameras_json, images_dir, max_views=max_video_views,
            )
            additions.append((generated, generated_rgb))
            wall_stats.append({
                "plane": name, "source_points": int(near_mask.sum()), "filled_points": len(generated),
                "video_color_views": video_views,
            })
    if additions:
        add_xyz = np.concatenate([item[0] for item in additions])
        add_rgb = np.concatenate([item[1] for item in additions])
        extra = np.empty(len(add_xyz), dtype=DTYPE)
        for index, name in enumerate(("x", "y", "z")):
            extra[name] = add_xyz[:, index]
        for index, name in enumerate(("red", "green", "blue")):
            extra[name] = add_rgb[:, index]
        output = np.concatenate([display_rows, extra])
    else:
        output = display_rows
    write_float_ply(output_ply, output)
    diagnostics = {
        "schema_version": 1, "display_only": True,
        "excluded_from_measurement_and_risk": True, "cell_m": cell,
        "source_points": len(rows), "output_points": len(output), "walls": wall_stats,
        "removed_ceiling_points": int((~display_keep).sum()),
        "snapped_floor_points": int(floor_snap.sum()),
        "snapped_wall_points": wall_snap_count,
    }
    output_ply.with_suffix(".json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    return diagnostics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_ply", type=Path)
    parser.add_argument("structure_json", type=Path)
    parser.add_argument("output_ply", type=Path)
    parser.add_argument("--cell", type=float, default=0.015)
    parser.add_argument("--cameras-json", type=Path)
    parser.add_argument("--images-dir", type=Path)
    parser.add_argument("--max-video-views", type=int, default=60)
    args = parser.parse_args()
    print(json.dumps(complete(
        args.input_ply, args.structure_json, args.output_ply, cell=args.cell,
        cameras_json=args.cameras_json, images_dir=args.images_dir,
        max_video_views=args.max_video_views,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

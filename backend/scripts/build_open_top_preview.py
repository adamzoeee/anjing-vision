"""Build a dense, observation-only web preview with the ceiling removed.

This script never synthesizes points and never changes the metric source cloud.
It uses the already reconstructed dense cloud, detects the dominant horizontal
ceiling plane, removes only points close to that plane, and writes a display
copy.  Walls, doors, windows, furniture and floor points are preserved.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np


_PLY_TYPES = {
    "char": "i1", "uchar": "u1", "short": "<i2", "ushort": "<u2",
    "int": "<i4", "uint": "<u4", "float": "<f4", "double": "<f8",
}


def _read_binary_vertices(source: Path) -> tuple[bytes, np.ndarray, np.ndarray]:
    """Read binary PLY records without converting colour attributes."""
    raw = source.read_bytes()
    marker = b"end_header"
    marker_end = raw.index(marker) + len(marker)
    if raw[marker_end:marker_end + 2] == b"\r\n":
        data_offset = marker_end + 2
    else:
        data_offset = marker_end + 1
    header = raw[:data_offset]
    text = header.decode("ascii")
    if "format binary_little_endian 1.0" not in text:
        raise ValueError("only binary_little_endian PLY is supported")
    count_match = re.search(r"(?m)^element vertex (\d+)\s*$", text)
    if not count_match:
        raise ValueError("PLY vertex count is missing")
    count = int(count_match.group(1))
    properties: list[tuple[str, str]] = []
    in_vertices = False
    for line in text.splitlines():
        parts = line.split()
        if parts[:2] == ["element", "vertex"]:
            in_vertices = True
        elif parts and parts[0] == "element":
            in_vertices = False
        elif in_vertices and len(parts) == 3 and parts[0] == "property":
            properties.append((parts[2], _PLY_TYPES[parts[1]]))
    dtype = np.dtype(properties)
    records = np.frombuffer(raw, dtype=dtype, count=count, offset=data_offset)
    points = np.column_stack((records["x"], records["y"], records["z"])).astype(np.float64)
    return header, records, points


def build(source: Path, output: Path) -> dict:
    import open3d as o3d

    source = Path(source)
    output = Path(output)
    header, records, points = _read_binary_vertices(source)
    if len(points) < 1000:
        raise ValueError(f"point cloud is empty or too small: {source}")

    z = points[:, 2]
    # 只在最高 8% 的点中找顶面。长视频融合后，床面/桌面可能比天花板
    # 拥有更多点；过低的分位数会把家具顶误判成天花板。
    high_cut = float(np.quantile(z, 0.92))
    high_indices = np.flatnonzero(z >= high_cut)
    high_cloud = o3d.geometry.PointCloud()
    high_cloud.points = o3d.utility.Vector3dVector(points[high_indices])
    plane, local_inliers = high_cloud.segment_plane(
        distance_threshold=0.025, ransac_n=3, num_iterations=700,
    )
    normal = np.asarray(plane[:3], dtype=np.float64)
    normal /= max(float(np.linalg.norm(normal)), 1e-12)
    if abs(float(normal[2])) < 0.88:
        raise ValueError(f"dominant upper plane is not horizontal: normal={normal.tolist()}")

    plane_height = float(-plane[3] / plane[2])
    if plane_height < float(np.quantile(z, 0.88)):
        raise ValueError(
            f"detected ceiling is implausibly low: height={plane_height:.3f}"
        )
    # 不能再用全局 z 截断：它会连墙顶、门框和高书架一起删掉。只在房间
    # 上部估计局部法线，并删除近水平的顶面观测；竖直墙面仍完整保留。
    high_cloud.estimate_normals(
        search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=0.06, max_nn=30),
    )
    high_normals = np.asarray(high_cloud.normals)
    horizontal = np.abs(high_normals[:, 2]) >= 0.82
    ceiling_like = horizontal & (points[high_indices, 2] >= plane_height - 0.20)
    remove = np.zeros(len(points), dtype=bool)
    remove[high_indices[ceiling_like]] = True
    kept = np.flatnonzero(~remove)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Preserve every original binary vertex field verbatim (especially float
    # RGB).  Open3D interprets float colours as 0..255 on read and would divide
    # the already-normalised 0..1 values a second time, producing a black web
    # preview.
    new_header = re.sub(
        rb"(?m)^element vertex \d+\s*$",
        f"element vertex {len(kept)}".encode("ascii"), header,
    )
    with output.open("wb") as handle:
        handle.write(new_header)
        handle.write(records[kept].tobytes())
    result = {
        "status": "passed" if len(kept) >= 100_000 and remove.mean() <= 0.30 else "failed",
        "method": "observation_fusion_horizontal_ceiling_normal_filter",
        "observation_only": True,
        "source_points": int(len(points)),
        "kept_points": int(len(kept)),
        "removed_ceiling_points": int(remove.sum()),
        "ceiling_height": plane_height,
        "output": str(output),
    }
    output.with_suffix(".json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(build(args.source, args.output))


if __name__ == "__main__":
    main()

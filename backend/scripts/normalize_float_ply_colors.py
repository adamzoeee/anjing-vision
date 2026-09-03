"""Convert Open3D float-RGB PLY files to correctly normalized uchar RGB."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import open3d as o3d


def normalize(source: Path, output: Path) -> None:
    cloud = o3d.io.read_point_cloud(str(source))
    colors = np.asarray(cloud.colors, dtype=np.float64)
    if len(colors) == 0:
        raise RuntimeError(f"PLY has no colors: {source}")
    # Open3D divides float PLY color properties by 255 while reading. The legacy
    # scan files already store normalized 0..1 floats, so undo that extra divide.
    if float(np.quantile(colors, 0.99)) <= 0.01:
        corrected = np.clip(colors * 255.0, 0.0, 1.0)
    else:
        corrected = np.clip(colors, 0.0, 1.0)
    cloud.colors = o3d.utility.Vector3dVector(corrected)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not o3d.io.write_point_cloud(str(output), cloud, write_ascii=False, compressed=False):
        raise RuntimeError(f"Failed to write {output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    normalize(args.source, args.output)


if __name__ == "__main__":
    main()

"""把 gsplat 激活值 PLY 转为 GaussianSplats3D/INRIA 网页查看器格式。

只转换参数表示，不改变 Gaussian 数量、位置或训练结果：
- scale: 线性尺度 -> log(scale)
- opacity: [0, 1] -> logit(opacity)
- quaternion: gsplat wxyz -> Three.js xyzw
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np

def convert(source: Path, target: Path) -> dict[str, int | str]:
    raw = source.read_bytes()
    header_end = raw.find(b"end_header\n")
    if header_end < 0:
        raise ValueError("PLY header 不完整")
    data_offset = header_end + len(b"end_header\n")
    header = raw[:data_offset].decode("ascii")
    match = re.search(r"element vertex (\d+)", header)
    if not match:
        raise ValueError("PLY 缺少 vertex 数量")
    count = int(match.group(1))
    properties = [line.split()[-1] for line in header.splitlines() if line.startswith("property ")]
    expected = data_offset + count * len(properties) * 4
    if len(raw) != expected:
        raise ValueError(f"PLY 长度不符：实际 {len(raw)}，预期 {expected}")

    values = np.frombuffer(raw, dtype="<f4", offset=data_offset).reshape(count, len(properties)).copy()
    index = {name: i for i, name in enumerate(properties)}
    scale_cols = [index[f"scale_{i}"] for i in range(3)]
    values[:, scale_cols] = np.log(np.clip(values[:, scale_cols], 1e-12, None))
    opacity_col = index["opacity"]
    opacity = np.clip(values[:, opacity_col], 1e-6, 1.0 - 1e-6)
    values[:, opacity_col] = np.log(opacity / (1.0 - opacity))
    rotation_cols = [index[f"rot_{i}"] for i in range(4)]
    rotation = values[:, rotation_cols].copy()  # gsplat: w, x, y, z
    values[:, rotation_cols] = rotation[:, [1, 2, 3, 0]]  # Three.js: x, y, z, w

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(raw[:data_offset] + values.astype("<f4", copy=False).tobytes())
    temporary.replace(target)
    return {"source": str(source), "target": str(target), "gaussians": count, "bytes": target.stat().st_size}


def convert_splat(source: Path, target: Path) -> dict[str, int | str]:
    """生成无需 PLY 参数解析的 32-byte/splat 网页文件。"""
    raw = source.read_bytes()
    data_offset = raw.find(b"end_header\n") + len(b"end_header\n")
    header = raw[:data_offset].decode("ascii")
    count = int(re.search(r"element vertex (\d+)", header).group(1))
    properties = [line.split()[-1] for line in header.splitlines() if line.startswith("property ")]
    values = np.frombuffer(raw, dtype="<f4", offset=data_offset).reshape(count, len(properties))
    index = {name: i for i, name in enumerate(properties)}

    rows = np.empty((count, 32), dtype=np.uint8)
    centers = np.ascontiguousarray(values[:, [index["x"], index["y"], index["z"]]], dtype="<f4")
    scales = np.ascontiguousarray(values[:, [index["scale_0"], index["scale_1"], index["scale_2"]]], dtype="<f4")
    rows[:, 0:12] = centers.view(np.uint8).reshape(count, 12)
    rows[:, 12:24] = scales.view(np.uint8).reshape(count, 12)
    rgb = np.clip(values[:, [index["f_dc_0"], index["f_dc_1"], index["f_dc_2"]]] * 255.0, 0, 255).astype(np.uint8)
    alpha = np.clip(values[:, index["opacity"]] * 255.0, 0, 255).astype(np.uint8)
    rows[:, 24:27] = rgb
    rows[:, 27] = alpha
    quat = values[:, [index["rot_0"], index["rot_1"], index["rot_2"], index["rot_3"]]]
    quat /= np.maximum(np.linalg.norm(quat, axis=1, keepdims=True), 1e-12)
    rows[:, 28:32] = np.clip(quat * 128.0 + 128.0, 0, 255).astype(np.uint8)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(rows.tobytes())
    temporary.replace(target)
    return {"source": str(source), "target": str(target), "gaussians": count, "bytes": target.stat().st_size}


if __name__ == "__main__":
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_name("gaussian_web.ply")
    print(convert_splat(src, dst) if dst.suffix.lower() == ".splat" else convert(src, dst))

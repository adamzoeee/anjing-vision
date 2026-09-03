"""SpatialLM1.1-Qwen-0.5B 空间结构识别适配层。

调用独立的 spatiallm conda 环境（E:\\PJs\\spatiallm 源码 + ModelScope 权重）：
  1. subprocess 运行 inference.py（detect_type=all：墙/门/窗/家具框）；
  2. 解析 layout 文本 → viewer/下游统一 JSON（米制、z-up、墙面贴轴坐标系）。
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import subprocess
import time
from functools import lru_cache
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv

    load_dotenv(_BACKEND_ROOT / ".env", override=False)
except ImportError:
    pass

logger = logging.getLogger("anjing.pipeline.spatiallm")

SPATIALLM_DIR = Path(os.getenv("SPATIALLM_DIR", r"E:\.PJs\spatiallm")).resolve()
SPATIALLM_CONDA_ENV = os.getenv("SPATIALLM_CONDA_ENV", "spatiallm")
SPATIALLM_MODEL_PATH = Path(
    os.getenv("SPATIALLM_MODEL_PATH", r"E:\.PJs\models\SpatialLM1.1-Qwen-0.5B")
)
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("SPATIALLM_TIMEOUT_SECONDS", "0") or 0)

_CATEGORY_COLORS = {
    "wall": "#8ecae6", "door": "#ffb703", "window": "#80ed99", "box": "#ff8fa3",
}


@lru_cache(maxsize=1)
def spatiallm_env_python() -> Path:
    """定位 spatiallm conda 环境的 python.exe（可用 SPATIALLM_PYTHON 覆盖）。"""
    configured = os.getenv("SPATIALLM_PYTHON")
    if configured:
        return Path(configured)
    candidates: list[Path] = []
    for root in (
        Path(os.environ.get("USERPROFILE", "C:\\")) / ".conda",
        Path(os.environ.get("USERPROFILE", "C:\\")) / "miniconda3",
        Path(os.environ.get("USERPROFILE", "C:\\")) / "anaconda3",
        Path("C:\\") / "anaconda3",
        Path("C:\\") / "ProgramData" / "miniconda3",
        Path("C:\\") / "ProgramData" / "anaconda3",
    ):
        candidates.append(root / "envs" / SPATIALLM_CONDA_ENV / "python.exe")
    try:
        base = subprocess.run(
            ["conda", "info", "--base"],
            capture_output=True, text=True, check=False, timeout=60,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        base = ""
    if base:
        candidates.append(Path(base) / "envs" / SPATIALLM_CONDA_ENV / "python.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        "未找到 spatiallm conda 环境（python.exe 不存在）。"
        "请先创建 spatiallm 环境（torch 2.7 cu128 + transformers + spconv + torch-scatter）"
        "或设置 SPATIALLM_PYTHON。"
    )


def _child_environment() -> dict:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    python = spatiallm_env_python()
    env_root = python.parent.parent
    extra = [str(env_root / "Library" / "bin"), str(env_root / "Scripts"), str(env_root), str(python.parent)]
    env["PATH"] = os.pathsep.join([*extra, env.get("PATH", "")])
    return env


def run_inference(
    point_cloud: Path,
    output_txt: Path,
    *,
    detect_type: str = "all",
    categories: list[str] | None = None,
    temperature: float = 0.4,
    top_k: int = 5,
    progress_callback=None,
    kill_check=None,
    timeout_s: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """运行 SpatialLM 推理，返回 {"layout_txt", "boxes_json", "boxes", "seconds"}。"""
    point_cloud = Path(point_cloud).resolve()
    output_txt = Path(output_txt).resolve()  # inference.py 以仓库目录为 cwd，必须绝对路径
    output_txt.parent.mkdir(parents=True, exist_ok=True)
    model_path = Path(SPATIALLM_MODEL_PATH)
    if not (model_path / "config.json").is_file():
        raise RuntimeError(f"SpatialLM 权重不存在：{model_path}")

    command = [
        str(spatiallm_env_python()),
        str(SPATIALLM_DIR / "inference.py"),
        "-p", str(point_cloud),
        "-o", str(output_txt),
        "-m", str(model_path),
        "-d", detect_type,
        "--seed", "0",
        "--temperature", str(float(temperature)),
        "--top_k", str(int(top_k)),
    ]
    if categories:
        command += ["-c", *categories]
    started = time.perf_counter()
    logger.info("spatiallm_start pcd=%s command=%s", point_cloud, " ".join(command))
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=_child_environment(),
        cwd=str(SPATIALLM_DIR),
    )
    assert process.stdout is not None
    tail: list[str] = []
    for raw in process.stdout:
        line = raw.rstrip("\r\n")
        if "\r" in line:
            line = line.split("\r")[-1]
        tail.append(line)
        tail = tail[-60:]
        if progress_callback is not None:
            progress_callback(line)
        if kill_check is not None and kill_check():
            process.terminate()
            raise RuntimeError("SpatialLM 推理被取消")
        if timeout_s and time.perf_counter() - started > timeout_s:
            process.terminate()
            raise RuntimeError(f"SpatialLM 推理超过 {timeout_s:.0f} 秒限制")
    return_code = process.wait()
    if return_code != 0:
        detail = "\n".join(tail)
        logger.error("spatiallm_failed exit=%s\n%s", return_code, detail)
        raise RuntimeError(f"SpatialLM 推理失败（退出码 {return_code}），日志尾部：\n{detail[-2000:]}")
    if not output_txt.is_file():
        raise RuntimeError("SpatialLM 未产出 layout 文件")
    elapsed = time.perf_counter() - started
    boxes = parse_layout(output_txt)
    boxes = _filter_objects(point_cloud, boxes)
    boxes_json = output_txt.with_suffix(".json")
    payload = {
        "backend": "spatiallm1.1-qwen-0.5b",
        "detect_type": detect_type,
        "categories": categories or [],
        "coordinate_unit": "meters",
        "z_up": True,
        "seconds": round(elapsed, 1),
        "counts": {
            "walls": len(boxes["walls"]),
            "doors": len(boxes["doors"]),
            "windows": len(boxes["windows"]),
            "objects": len(boxes["objects"]),
        },
        **boxes,
    }
    boxes_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "spatiallm_done seconds=%.1f walls=%d doors=%d windows=%d objects=%d",
        elapsed, len(boxes["walls"]), len(boxes["doors"]), len(boxes["windows"]), len(boxes["objects"]),
    )
    return {"layout_txt": output_txt, "boxes_json": boxes_json, "boxes": boxes, "seconds": elapsed}


def _floats(text: str, count: int) -> list[float]:
    values = [float(part.strip()) for part in text.split(",")[:count]]
    if len(values) != count:
        raise ValueError(f"expected {count} numbers, got {values}")
    return values


_OBJECT_WHITELIST = tuple(
    name.strip().lower()
    for name in os.getenv("SPATIALLM_OBJECT_WHITELIST", "").split(",")
    if name.strip()
)


def _filter_objects(point_cloud: Path, boxes: dict) -> dict:
    """过滤 SpatialLM 幻觉物体框。

    1. 若配置 SPATIALLM_OBJECT_WHITELIST（逗号分隔），仅保留白名单类别；
    2. 密度支撑门槛：统计对齐点云在框内的点数，低于 min_points 的框视为
       凭空生成的幻觉（如不存在的 washing_machine）并丢弃。
    """
    objects = boxes.get("objects", [])
    if not objects:
        return boxes
    try:
        import numpy as np
        import open3d as o3d

        pcd = o3d.io.read_point_cloud(str(point_cloud))
        pts = np.asarray(pcd.points, dtype=np.float64)
    except Exception as exc:  # noqa: BLE001 - 过滤失败不阻断识别结果
        logger.warning("spatiallm_object_filter_unavailable: %s", exc)
        return boxes

    kept: list[dict] = []
    for item in objects:
        category = str(item.get("category", "")).lower()
        if _OBJECT_WHITELIST and category not in _OBJECT_WHITELIST:
            logger.info("drop object %s (not in whitelist)", category)
            continue
        center = np.asarray(item["center"], dtype=np.float64)
        half = np.asarray(item["size"], dtype=np.float64) / 2.0
        theta = math.radians(float(item.get("rotation_z_deg", 0.0)))
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        delta = pts - center
        local_x = delta[:, 0] * cos_t + delta[:, 1] * sin_t
        local_y = -delta[:, 0] * sin_t + delta[:, 1] * cos_t
        inside = (
            (np.abs(local_x) <= max(half[0], 0.01))
            & (np.abs(local_y) <= max(half[1], 0.01))
            & (np.abs(delta[:, 2]) <= max(half[2], 0.01))
        )
        support = int(inside.sum())
        item = dict(item)
        item["support_points"] = support
        if support < 200:
            logger.info("drop object %s (support=%d, likely hallucination)", category, support)
            continue
        kept.append(item)
    boxes["objects"] = kept
    return boxes


def parse_layout(layout_txt: Path) -> dict:
    """解析 SpatialLM layout 文本 → 统一 3D 框 JSON（米制）。

    与 spatiallm.layout.Layout.from_str 使用相同的语法：
      wall_0=Wall(ax,ay,az,bx,by,bz,height,thickness)
      door_0=Door(wall_2,pos_x,pos_y,pos_z,width,height)
      window_0=Window(wall_3,pos_x,pos_y,pos_z,width,height)
      bbox_0=Bbox(class_name,pos_x,pos_y,pos_z,angle_z,scale_x,scale_y,scale_z)

    墙以线段 (a,b,height,thickness) 表示 → 转为带朝向的框；
    门/窗挂在其所属墙上 → 以墙朝向为框朝向。
    """
    text = Path(layout_txt).read_text(encoding="utf-8", errors="replace")

    walls: dict[int, dict] = {}
    pending_children: list[tuple[str, str]] = []
    objects: list[dict] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        try:
            label, rest = line.split("=", 1)
            entity_kind = label.split("_")[0]
            entity_id = int(label.split("_")[1])
            start = rest.find("(")
            end = rest.rfind(")")
            if start < 0 or end < 0:
                continue
            params = [part.strip() for part in rest[start + 1 : end].split(",")]

            if entity_kind == "wall" and len(params) >= 8:
                ax, ay, az, bx, by, bz, height, thickness = _floats(",".join(params[:8]), 8)
                direction = (bx - ax, by - ay, bz - az)
                length = math.hypot(direction[0], direction[1], direction[2])
                if length < 1e-6:
                    continue
                angle_z = math.degrees(math.atan2(direction[1], direction[0]))
                walls[entity_id] = {
                    "id": entity_id,
                    "center": [(ax + bx) / 2, (ay + by) / 2, (az + bz) / 2 + height / 2],
                    "size": [max(length, 0.01), max(thickness, 0.05), max(height, 0.01)],
                    "rotation_z_deg": angle_z,
                    "height": height,
                    "thickness": thickness,
                }
            elif entity_kind in ("door", "window") and len(params) >= 6:
                # params[0] 为 wall_id，params[1:6] 为 pos_x,pos_y,pos_z,width,height
                pending_children.append((entity_kind, ",".join(params)))
            elif entity_kind == "bbox" and len(params) >= 8:
                category = params[0]
                px, py, pz, angle_z, sx, sy, sz = (float(part) for part in params[1:8])
                objects.append({
                    "kind": "object",
                    "category": category,
                    "center": [px, py, pz],
                    "size": [max(sx, 0.01), max(sy, 0.01), max(sz, 0.01)],
                    "rotation_z_deg": angle_z,
                })
        except (ValueError, IndexError):
            continue

    doors, windows = [], []
    for kind, joined in pending_children:
        params = [part.strip() for part in joined.split(",")]
        try:
            wall_id = int(params[0].split("_")[-1])
            wall = walls.get(wall_id)
            if wall is None:
                continue
            px, py, pz, width, height = _floats(",".join(params[1:6]), 5)
            item = {
                "kind": kind,
                "center": [px, py, pz],
                "size": [max(width, 0.01), 0.15, max(height, 0.01)],
                "rotation_z_deg": wall["rotation_z_deg"],
                "wall_id": wall_id,
            }
            (doors if kind == "door" else windows).append(item)
        except (ValueError, IndexError):
            continue

    def _sort(items):
        return sorted(items, key=lambda item: (item["center"][0], item["center"][1]))

    def _dedup(items: list[dict]) -> list[dict]:
        """合并几乎重合的同类别框（VLM 自回归偶发的重复输出）。"""
        kept: list[dict] = []
        for item in sorted(items, key=lambda x: (x.get("category", ""), tuple(x["center"]))):
            duplicate = False
            for existing in kept:
                if existing.get("category") != item.get("category"):
                    continue
                delta = math.dist(item["center"], existing["center"])
                size_min = min(item["size"][0], existing["size"][0]) / 2
                if delta < max(0.15, size_min):
                    # 重复：保留体积更大者（或合并中心）
                    if item["size"][0] * item["size"][1] * item["size"][2] > \
                            existing["size"][0] * existing["size"][1] * existing["size"][2]:
                        existing.update(item)
                    duplicate = True
                    break
            if not duplicate:
                kept.append(dict(item))
        return kept

    return {
        "walls": _sort(list(walls.values())),
        "doors": _sort(_dedup(doors)),
        "windows": _sort(_dedup(windows)),
        "objects": _sort(_dedup(objects)),
    }

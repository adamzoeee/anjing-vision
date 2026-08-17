"""vid2scene 重建适配层（vid2scene 为 Apache-2.0 许可）。

自研「抽帧 → pycolmap SfM → gsplat 训练」整体替换为 vid2scene 端到端重建：
  1. subprocess 调用 vid2scene_core/vid2scene.py CLI（独立 conda 环境）；
  2. 解析其产物（sfm_output/sparse/0 COLMAP 模型 + ply/splat.ply）为本管线下游契约。

下游 exporter / calibrator / geometry / rules / report 全部复用，不感知重建来源。
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from functools import lru_cache
from pathlib import Path

import numpy as np

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
try:  # CLI/worker 直跑时也读取 backend/.env 中的 VID2SCENE_* 配置（FastAPI 已加载时无副作用）
    from dotenv import load_dotenv

    load_dotenv(_BACKEND_ROOT / ".env", override=False)
except ImportError:  # 无 dotenv 时退化为纯环境变量
    pass

logger = logging.getLogger("anjing.pipeline.vid2scene")

SH_C0 = 0.28209479177387814

_DEFAULT_VID2SCENE_ROOT = Path(__file__).resolve().parents[2].parent / "vid2scene"
VID2SCENE_CORE_DIR = Path(
    os.getenv("VID2SCENE_CORE_DIR", str(_DEFAULT_VID2SCENE_ROOT / "vid2scene_core"))
)
VID2SCENE_GSPLAT_SCRIPT = Path(
    os.getenv(
        "VID2SCENE_GSPLAT_SCRIPT",
        str(_DEFAULT_VID2SCENE_ROOT / "gsplat" / "examples" / "simple_trainer.py"),
    )
)
VID2SCENE_CONDA_ENV = os.getenv("VID2SCENE_CONDA_ENV", "vid2scene")
DEFAULT_FRAMECOUNT = int(os.getenv("VID2SCENE_FRAMECOUNT", "300"))
DEFAULT_TRAINING_STEPS = int(os.getenv("VID2SCENE_TRAINING_STEPS", "20000"))
DEFAULT_MAX_GAUSSIANS = int(os.getenv("VID2SCENE_MAX_GAUSSIANS", "1200000"))
DEFAULT_METHOD = os.getenv("VID2SCENE_RECONSTRUCTION_METHOD", "colmap")
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("VID2SCENE_TIMEOUT_SECONDS", "0") or 0)
APRILTAG_ENABLED = os.getenv("APRILTAG_ENABLED", "true").strip().lower() not in {
    "0", "false", "no",
}
APRILTAG_FAMILY = os.getenv("APRILTAG_FAMILY", "tagStandard41h12").strip()
APRILTAG_SIZE_M = float(os.getenv("APRILTAG_SIZE_M", "0.09"))
_APRILTAG_SCALE_PATTERN = re.compile(r"Applied scale factor:\s*([0-9.eE+-]+)")

# stdout 标记 → 重建阶段内的进度（0..1）。gsplat 的 step 行另行解析。
_STAGE_MARKERS = [
    ("Extracting frames", 0.05),
    ("Doing retrieval", 0.15),
    ("Doing pairs", 0.20),
    ("Doing features", 0.30),
    ("Doing matches", 0.40),
    ("Running 3D reconstruction", 0.45),
    ("Reconstructed", 0.50),
    ("Orientation alignment", 0.52),
    ("Running Gsplat script", 0.55),
]
_FFMPEG_DIRS = (r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin")


@lru_cache(maxsize=1)
def vid2scene_env_python() -> Path:
    """定位 vid2scene conda 环境的 python.exe（可用 VID2SCENE_PYTHON 覆盖）。

    依次尝试：显式环境变量 → 常见 conda 安装目录 → conda info --base。
    不依赖 PATH 中存在 conda（后台 worker 环境的 PATH 可能不包含它）。
    """
    configured = os.getenv("VID2SCENE_PYTHON")
    if configured:
        return Path(configured)
    candidates: list[Path] = []
    for root in (
        Path(os.environ.get("USERPROFILE", "C:\\")) / ".conda",
        Path(os.environ.get("USERPROFILE", "C:\\")) / "miniconda3",
        Path(os.environ.get("USERPROFILE", "C:\\")) / "anaconda3",
        Path("C:\\") / "ProgramData" / "miniconda3",
        Path("C:\\") / "ProgramData" / "anaconda3",
    ):
        candidates.append(root / "envs" / VID2SCENE_CONDA_ENV / "python.exe")
    try:
        base = subprocess.run(
            ["conda", "info", "--base"],
            capture_output=True, text=True, check=False, timeout=60,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - conda 不在 PATH 时落到候选路径兜底
        base = ""
    if base:
        candidates.append(Path(base) / "envs" / VID2SCENE_CONDA_ENV / "python.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        "未找到 vid2scene conda 环境（python.exe 不存在）。"
        "请先运行 backend/scripts/vid2scene/setup_vid2scene.ps1 或设置 VID2SCENE_PYTHON。"
    )


def _child_environment() -> dict:
    """构造子进程环境：conda env 的 bin 目录（colmap CLI）+ 本机 ffmpeg。

    显式清空 PYTHONPATH/PYTHONHOME，保证 vid2scene 只使用自己的 conda 环境，
    与调用方（本后端 venv）完全隔离。
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    python = vid2scene_env_python()
    # Conda on Windows places python.exe directly in the environment root.
    env_root = python.parent
    extra = [
        str(env_root / "Library" / "bin"),
        str(env_root / "Scripts"),
        str(env_root),
        str(python.parent),
    ]
    extra += [d for d in _FFMPEG_DIRS if os.path.isdir(d)]
    current = env.get("PATH", "")
    env["PATH"] = os.pathsep.join([*extra, current])
    env["GSPLAT_SCRIPT"] = str(VID2SCENE_GSPLAT_SCRIPT)

    # Keep temporary files and any lazily rebuilt CUDA extensions on the
    # project drive.  Windows always defines TEMP/TMP, so the values in the
    # project's .env (loaded with override=False) cannot replace the C: drive
    # defaults by themselves.
    project_root = _BACKEND_ROOT.parent
    temp_dir = project_root / ".tmp"
    torch_extensions_dir = project_root / ".cache" / "torch_extensions"
    cuda_cache_dir = project_root / ".cache" / "cuda"
    for directory in (temp_dir, torch_extensions_dir, cuda_cache_dir):
        directory.mkdir(parents=True, exist_ok=True)
    env["TEMP"] = str(temp_dir)
    env["TMP"] = str(temp_dir)
    env["TORCH_EXTENSIONS_DIR"] = str(torch_extensions_dir)
    env["CUDA_CACHE_PATH"] = str(cuda_cache_dir)
    return env


def build_command(
    source: Path,
    output_dir: Path,
    *,
    target_framecount: int = DEFAULT_FRAMECOUNT,
    training_num_steps: int = DEFAULT_TRAINING_STEPS,
    max_gaussians: int = DEFAULT_MAX_GAUSSIANS,
    reconstruction_method: str = DEFAULT_METHOD,
    apriltag_enabled: bool = APRILTAG_ENABLED,
    apriltag_size_m: float = APRILTAG_SIZE_M,
) -> list[str]:
    """构造 vid2scene CLI 命令（供测试断言与实际调用共用）。"""
    if apriltag_enabled:
        if APRILTAG_FAMILY != "tagStandard41h12":
            raise ValueError("vid2scene 当前仅支持 tagStandard41h12 尺度标定")
        if not 0.0 < apriltag_size_m <= 1.0:
            raise ValueError("APRILTAG_SIZE_M 必须位于 (0, 1] 米")
    command = [
        str(vid2scene_env_python()),
        str(VID2SCENE_CORE_DIR / "vid2scene.py"),
        str(output_dir),
        "--target_framecount", str(int(target_framecount)),
        "--training_max_num_gaussians", str(int(max_gaussians)),
        "--training_num_steps", str(int(training_num_steps)),
        "--reconstruction_method", reconstruction_method,
        # 保留 COLMAP 世界坐标：下游几何测量/语义投影要求相机与点云同坐标系。
        "--no_normalize_world_space",
    ]
    command.extend(["--image_dir" if Path(source).is_dir() else "--video_path", str(source)])
    if apriltag_enabled:
        command.extend(["--apriltag_size", str(float(apriltag_size_m))])
    return command


def map_progress(line: str, training_num_steps: int = DEFAULT_TRAINING_STEPS) -> float | None:
    """把一行 stdout 映射为 0..1 的重建阶段进度；无法识别返回 None。"""
    for marker, progress in _STAGE_MARKERS:
        if marker in line:
            return progress
    if "step=" in line and training_num_steps > 0:
        try:
            step = int(line.split("step=", 1)[1].split()[0])
            return min(0.98, 0.55 + 0.43 * (step / training_num_steps))
        except (ValueError, IndexError):
            return None
    return None


def run_reconstruction(
    source: Path,
    work_dir: Path,
    *,
    target_framecount: int = DEFAULT_FRAMECOUNT,
    training_num_steps: int = DEFAULT_TRAINING_STEPS,
    max_gaussians: int = DEFAULT_MAX_GAUSSIANS,
    reconstruction_method: str = DEFAULT_METHOD,
    progress_callback=None,
    kill_check=None,
    timeout_s: float = DEFAULT_TIMEOUT_SECONDS,
    apriltag_enabled: bool = APRILTAG_ENABLED,
    apriltag_size_m: float = APRILTAG_SIZE_M,
) -> dict:
    """运行 vid2scene 重建并返回产物路径字典。

    progress_callback(progress: float, line: str) 每行 stdout 调用一次；
    kill_check() 返回 True 时终止子进程并抛出 RuntimeError。

    子进程 cwd 为 vid2scene_core：所有路径必须先转为绝对路径，
    否则调用方传入的相对路径（如 data/media/...）会解析失败。
    """
    source = Path(source).resolve()
    work_dir = Path(work_dir).resolve()
    command = build_command(
        source, work_dir,
        target_framecount=target_framecount,
        training_num_steps=training_num_steps,
        max_gaussians=max_gaussians,
        reconstruction_method=reconstruction_method,
        apriltag_enabled=apriltag_enabled,
        apriltag_size_m=apriltag_size_m,
    )
    started = time.perf_counter()
    logger.info(
        "vid2scene_start source=%s work_dir=%s framecount=%d steps=%d "
        "max_gaussians=%d method=%s command=%s",
        source, work_dir, target_framecount, training_num_steps,
        max_gaussians, reconstruction_method, " ".join(command),
    )
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=_child_environment(),
        cwd=str(VID2SCENE_CORE_DIR),
    )
    assert process.stdout is not None
    tail: list[str] = []
    apriltag_scale_factor: float | None = None
    for line in process.stdout:
        line = line.rstrip("\n")
        tail.append(line)
        tail = tail[-40:]
        scale_match = _APRILTAG_SCALE_PATTERN.search(line)
        if scale_match:
            apriltag_scale_factor = float(scale_match.group(1))
        if progress_callback is not None:
            progress = map_progress(line, training_num_steps)
            if progress is not None:
                progress_callback(progress)
        if kill_check is not None and kill_check():
            process.terminate()
            raise RuntimeError("vid2scene 重建被取消")
        if timeout_s and time.perf_counter() - started > timeout_s:
            process.terminate()
            raise RuntimeError(f"vid2scene 重建超过 {timeout_s:.0f} 秒限制")
    return_code = process.wait()
    if return_code != 0:
        detail = "\n".join(tail)
        logger.error("vid2scene_failed exit=%s\n%s", return_code, detail)
        raise RuntimeError(f"vid2scene 重建失败（退出码 {return_code}），日志尾部：\n{detail[-2000:]}")
    if apriltag_enabled and apriltag_scale_factor is None:
        # 标定失败降级：记录原因，继续以相对尺度返回重建结果，
        # 下游管道据此生成相对尺度报告（不中止重建）。
        failed_calibration = {
            "status": "calibration_failed",
            "coordinate_unit": "model_units",
            "family": APRILTAG_FAMILY,
            "tag_size_m": float(apriltag_size_m),
            "scale_factor": None,
            "scale_applied_by": None,
        }
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "metric_calibration.json").write_text(
            json.dumps(failed_calibration, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.warning(
            "apriltag_calibration_failed family=%s tag_size_m=%s -> relative scale",
            APRILTAG_FAMILY,
            apriltag_size_m,
        )
    elapsed = time.perf_counter() - started
    calibrated = apriltag_enabled and apriltag_scale_factor is not None
    calibration = {
        "status": (
            "metric_apriltag"
            if calibrated
            else ("calibration_failed" if apriltag_enabled else "relative")
        ),
        "coordinate_unit": "meters" if calibrated else "model_units",
        "family": APRILTAG_FAMILY if calibrated else None,
        "tag_size_m": float(apriltag_size_m) if calibrated else None,
        "scale_factor": apriltag_scale_factor,
        "scale_applied_by": "vid2scene" if calibrated else None,
    }
    (work_dir / "metric_calibration.json").write_text(
        json.dumps(calibration, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("vid2scene_done seconds=%.1f work_dir=%s", elapsed, work_dir)
    return {
        "sfm_dir": work_dir / "sfm_output",
        "sparse_dir": work_dir / "sfm_output" / "sparse" / "0",
        "image_dir": work_dir / "sfm_output" / "images",
        "splat_ply": work_dir / "ply" / "splat.ply",
        "result_dir": work_dir / "results",
        "seconds": elapsed,
        "metric_calibration": calibration,
    }


def parse_sparse_model(sparse_dir: Path) -> dict:
    """用本环境 pycolmap 解析 vid2scene 的 COLMAP 稀疏模型为下游契约。

    返回 {"cameras": [{name,R,t,K,center,camera_model,camera_params,
    radial_distortion,image_size,undistorted}], "points3D", "colors3D", "quality"}。
    """
    sparse_dir = Path(sparse_dir)
    import pycolmap

    recon = pycolmap.Reconstruction(str(sparse_dir))
    cameras, points, colors, errors, track_lengths = [], [], [], [], []
    for img_id in recon.images:
        img = recon.images[img_id]
        cam = recon.cameras[img.camera_id]
        pose = img.cam_from_world()
        rotation = np.asarray(pose.rotation.matrix(), dtype=np.float64)
        translation = np.asarray(pose.translation, dtype=np.float64).reshape(3)
        center = -rotation.T @ translation
        fx = cam.focal_length_x
        fy = cam.focal_length_y
        cx = cam.principal_point_x
        cy = cam.principal_point_y
        model_name = getattr(getattr(cam, "model", None), "name", None) or str(
            getattr(cam, "model_name", "PINHOLE")
        )
        params = np.asarray(getattr(cam, "params", []), dtype=np.float64)
        radial = np.zeros(0, dtype=np.float64)
        if model_name == "SIMPLE_RADIAL" and params.size >= 4:
            radial = params[3:4]
        elif model_name == "RADIAL" and params.size >= 5:
            radial = params[3:5]
        cameras.append({
            "name": img.name,
            "R": rotation,
            "t": translation,
            "K": np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]]),
            "center": center,
            "camera_model": model_name,
            "camera_params": params,
            "radial_distortion": radial,
            "image_size": [int(getattr(cam, "width", 0)), int(getattr(cam, "height", 0))],
            "undistorted": model_name in {"PINHOLE", "SIMPLE_PINHOLE"},
        })
    for pid in recon.points3D:
        point = recon.points3D[pid]
        points.append(point.xyz)
        colors.append(getattr(point, "color", [128, 128, 128]))
        errors.append(float(getattr(point, "error", np.nan)))
        track = getattr(point, "track", None)
        track_lengths.append(
            len(track.elements) if track is not None and hasattr(track, "elements") else 0
        )
    finite = np.asarray(errors, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    return {
        "cameras": cameras,
        "points3D": np.asarray(points, dtype=np.float64).reshape(-1, 3),
        "colors3D": np.asarray(colors, dtype=np.uint8).reshape(-1, 3),
        "quality": {
            "registered_images": len(cameras),
            "points3D": len(points),
            "median_reprojection_error": float(np.median(finite)) if len(finite) else None,
            "mean_reprojection_error": float(np.mean(finite)) if len(finite) else None,
            "mean_track_length": float(np.mean(track_lengths)) if track_lengths else 0.0,
            "component_count": 1,
            "component_registered_images": [len(cameras)],
            "backend": "vid2scene",
        },
    }


def _read_binary_ply(ply_path: Path) -> tuple[np.ndarray, list[str]]:
    """极简二进制 PLY 读取器（只支持 float 属性），返回 (结构化数组, 属性名)。"""
    with open(ply_path, "rb") as handle:
        header = b""
        while not header.endswith(b"end_header\n"):
            header += handle.readline()
        lines = header.decode("ascii", "replace").splitlines()
        if "binary_little_endian" not in lines[1]:
            raise ValueError(f"不支持的 PLY 格式: {lines[1]}")
        names = [line.split()[-1] for line in lines if line.startswith("property float")]
        count = int(next(line for line in lines if line.startswith("element vertex")).split()[-1])
        dtype = np.dtype([(name, "<f4") for name in names])
        return np.fromfile(ply_path, dtype=dtype, offset=len(header), count=count), names


def read_splat_ply(ply_path: Path) -> dict:
    """解析 vid2scene 训练导出的 splat.ply（与我们的高斯导出同一字段布局）。

    返回 {"means": (N,3), "colors": (N,3) uint8, "opacities": (N,) sigmoid 值,
    "scales": (N,3), "quats": (N,4), "sh0": (N,3), "sh_rest": (N,45)}。
    """
    data, names = _read_binary_ply(Path(ply_path))
    means = np.stack([data[n] for n in ("x", "y", "z")], axis=1).astype(np.float64)
    sh0 = np.stack([data[n] for n in ("f_dc_0", "f_dc_1", "f_dc_2")], axis=1)
    colors = np.clip(sh0 * SH_C0 + 0.5, 0.0, 1.0)
    opacities = 1.0 / (1.0 + np.exp(-data["opacity"].astype(np.float64)))
    scales = np.stack([data[n] for n in ("scale_0", "scale_1", "scale_2")], axis=1)
    quats = np.stack([data[n] for n in ("rot_0", "rot_1", "rot_2", "rot_3")], axis=1)
    rest_names = [n for n in names if n.startswith("f_rest_")]
    sh_rest = np.stack([data[n] for n in rest_names], axis=1) if rest_names else np.zeros((len(means), 0))
    return {
        "means": means,
        "colors": (colors * 255.0 + 0.5).astype(np.uint8),
        "opacities": opacities,
        "scales": scales,
        "quats": quats,
        "sh0": sh0,
        "sh_rest": sh_rest,
    }


def point_cloud_from_splat(splat: dict, opacity_threshold: float = 0.01) -> tuple[np.ndarray, np.ndarray]:
    """从 splat 提取点云（可见高斯中心），供几何测量/标定/语义投影使用。"""
    keep = splat["opacities"] >= opacity_threshold
    if not keep.any():
        raise ValueError("splat.ply 中没有可见高斯点")
    return splat["means"][keep], splat["colors"][keep]


def read_training_stats(result_dir: Path) -> dict:
    """读取训练统计（results/stats/train_step*.json 中最新一步），尽力而为。"""
    result_dir = Path(result_dir)
    stats_files = sorted(result_dir.glob("stats/train_step*.json")) if result_dir.exists() else []
    if not stats_files:
        return {}
    try:
        return json.loads(stats_files[-1].read_text(encoding="utf-8", errors="replace"))
    except (ValueError, OSError):
        return {}


def parse_reconstruction(work_dir: Path) -> dict:
    """汇总 vid2scene 产物为下游契约。

    返回 {"cameras", "points3D", "colors3D", "quality", "gaussian",
    "image_dir", "splat_ply", "training_stats"}。
    """
    work_dir = Path(work_dir).resolve()
    sparse = parse_sparse_model(work_dir / "sfm_output" / "sparse" / "0")
    splat_path = work_dir / "ply" / "splat.ply"
    if not splat_path.is_file():
        raise FileNotFoundError(f"vid2scene 未产出 splat.ply: {splat_path}")
    splat = read_splat_ply(splat_path)
    stats = read_training_stats(work_dir / "results")
    calibration_path = work_dir / "metric_calibration.json"
    calibration = (
        json.loads(calibration_path.read_text(encoding="utf-8"))
        if calibration_path.is_file()
        else {
            "status": "relative",
            "coordinate_unit": "model_units",
            "scale_factor": None,
            "scale_applied_by": None,
        }
    )
    from .scene_contract import validate_metric_calibration
    validate_metric_calibration(calibration)
    return {
        **sparse,
        "gaussian": splat,
        "image_dir": work_dir / "sfm_output" / "images",
        "splat_ply": splat_path,
        "training_stats": stats,
        "metric_calibration": calibration,
        "coordinate_unit": calibration["coordinate_unit"],
        "metric_scale_status": calibration["status"],
    }

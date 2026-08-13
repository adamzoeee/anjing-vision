"""3DGS 训练：gsplat 1.5.x 光栅化 + Adam 优化，输入 SFM 相机位姿与图片。"""
import math
import importlib.util
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from scipy.spatial import cKDTree

logger = logging.getLogger("anjing.pipeline.trainer")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
# 复杂室内场景允许训练至 2 万步；不是固定跑满，而是在独立 holdout
# 连续无显著提升时动态提前结束。环境变量仍可覆盖，便于低配机器降档。
NUM_ITER = int(os.getenv("GAUSSIAN_TRAIN_ITERATIONS", "20000"))
SH_DEGREE = 3
SH_C0 = 0.28209479177387814
_DLL_HANDLES: list[object] = []


class ValidationEarlyStop:
    """质量优先的 holdout 平台期判断，同时保护平均与最差视角。"""

    def __init__(
        self,
        patience: int = 5,
        min_psnr_delta: float = 0.03,
        min_ssim_delta: float = 0.002,
        worst_view_tolerance: float = 0.05,
    ):
        self.patience = patience
        self.min_psnr_delta = min_psnr_delta
        self.min_ssim_delta = min_ssim_delta
        self.worst_view_tolerance = worst_view_tolerance
        self.best_psnr = -float("inf")
        self.best_ssim = -float("inf")
        self.best_min_psnr = -float("inf")
        self.stale = 0

    def update(
        self,
        psnr: float,
        *,
        ssim: float,
        min_psnr: float,
        refinement_finished: bool,
    ) -> bool:
        if not refinement_finished:
            self.best_psnr = psnr
            self.best_ssim = ssim
            self.best_min_psnr = min_psnr
            self.stale = 0
            return False
        worst_view_safe = min_psnr >= self.best_min_psnr - self.worst_view_tolerance
        improved = worst_view_safe and (
            psnr > self.best_psnr + self.min_psnr_delta
            or ssim > self.best_ssim + self.min_ssim_delta
            or min_psnr > self.best_min_psnr + self.min_psnr_delta
        )
        if improved:
            self.best_psnr = max(self.best_psnr, psnr)
            self.best_ssim = max(self.best_ssim, ssim)
            self.best_min_psnr = max(self.best_min_psnr, min_psnr)
            self.stale = 0
        else:
            self.stale += 1
        return self.stale >= self.patience


def _configure_msvc_environment() -> None:
    """Load the existing Visual Studio x64 build environment into this process."""
    if os.name != "nt" or shutil.which("cl"):
        return
    candidates = []
    configured = os.getenv("VCVARS64_PATH")
    if configured:
        candidates.append(Path(configured))
    candidates.append(
        Path(r"D:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat")
    )
    vswhere = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
    if vswhere.is_file():
        result = subprocess.run(
            [
                str(vswhere), "-latest", "-products", "*", "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        install_path = result.stdout.strip()
        if install_path:
            candidates.append(Path(install_path) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat")

    vcvars = next((path for path in candidates if path.is_file()), None)
    if vcvars is None:
        return
    result = subprocess.run(
        f'cmd.exe /d /c call "{vcvars}" >nul && set',
        capture_output=True,
        text=True,
        encoding="mbcs",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        logger.error("[3DGS Runtime] MSVC environment setup failed: %s", result.stderr.strip())
        return
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            if key:
                # Conda-launched Windows processes can contain both Path and PATH.
                # Normalize this key so the worker does not later read a stale PATH.
                os.environ["PATH" if key.lower() == "path" else key] = value


def _runtime_error(component: str, detail: str) -> RuntimeError:
    return RuntimeError(f"3DGS运行环境缺少 {component}：{detail}")


def _reuse_cached_gsplat_extension() -> None:
    """Windows 下优先复用已编译的 gsplat 扩展，避免每次任务重复 JIT。"""
    if os.name != "nt":
        return
    loaded_backend = sys.modules.get("gsplat.cuda._backend")
    if loaded_backend is not None and getattr(loaded_backend, "_C", None) is not None:
        return
    cache_root = os.getenv("TORCH_EXTENSIONS_DIR")
    if not cache_root:
        return
    extension = Path(cache_root) / "gsplat_cuda" / "gsplat_cuda.pyd"
    if not extension.is_file():
        return
    cuda_home = os.getenv("CUDA_HOME")
    if cuda_home and hasattr(os, "add_dll_directory"):
        _DLL_HANDLES.append(os.add_dll_directory(str(Path(cuda_home) / "bin")))
    spec = importlib.util.spec_from_file_location("gsplat_cuda", extension)
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import gsplat
    gsplat.csrc = module


def ensure_3dgs_runtime() -> None:
    """Configure and validate the local CUDA toolchain before 3DGS training."""
    repo_root = Path(__file__).resolve().parents[2]
    venv_scripts = repo_root / ".venv" / "Scripts"
    cuda_home = Path(r"E:\CUDA\v12.8")
    extensions_dir = repo_root / ".torch_extensions"
    temp_dir = repo_root / ".tmp"
    extensions_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    os.environ["CUDA_HOME"] = str(cuda_home)
    os.environ["TORCH_EXTENSIONS_DIR"] = str(extensions_dir)
    os.environ["TEMP"] = str(temp_dir)
    os.environ["TMP"] = str(temp_dir)
    # vcvars64 supplies INCLUDE/LIB and the exact versioned cl.exe directory.
    # Load it first because the batch file replaces PATH in some worker processes.
    _configure_msvc_environment()
    current_path = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join(
        [
            str(venv_scripts),
            str(cuda_home / "bin"),
            str(cuda_home / "lib" / "x64"),
            current_path,
        ]
    )

    # cpp_extension may have cached CUDA_HOME=None when torch was first imported.
    from torch.utils import cpp_extension
    cpp_extension.CUDA_HOME = str(cuda_home)

    resolved_tools = {
        "Ninja": shutil.which("ninja"),
        "NVCC": shutil.which("nvcc"),
        "MSVC": shutil.which("cl"),
    }
    checks = {
        "CUDA": torch.cuda.is_available(),
        **{name: path is not None for name, path in resolved_tools.items()},
    }
    for name, available in checks.items():
        logger.info("[3DGS Runtime] %s: %s", name, "OK" if available else "MISSING")
    for name, path in resolved_tools.items():
        if path:
            logger.info("[3DGS Runtime] %s path: %s", name, path)
    if not checks["CUDA"]:
        raise _runtime_error("CUDA", "PyTorch未检测到可用的NVIDIA GPU")
    if not checks["Ninja"]:
        raise _runtime_error("Ninja", f"未在 {venv_scripts} 找到 ninja.exe")
    if not checks["NVCC"]:
        raise _runtime_error("NVCC", f"未在 {cuda_home / 'bin'} 找到 nvcc.exe")
    if not checks["MSVC"]:
        raise _runtime_error("MSVC", "未找到 cl.exe，请确认Visual Studio C++生成工具已安装")

    try:
        import gsplat
        logger.info(
            "[3DGS Runtime] gsplat: OK (version=%s)",
            getattr(gsplat, "__version__", "unknown"),
        )
    except Exception as exc:  # noqa: BLE001 - report the real dependency error
        logger.exception("[3DGS Runtime] gsplat import failed")
        raise RuntimeError(f"gsplat导入失败：{type(exc).__name__}: {exc}") from exc

    try:
        _reuse_cached_gsplat_extension()
        from gsplat.cuda._backend import _C
        if _C is None:
            raise ImportError("gsplat CUDA extension returned None")
        logger.info("[3DGS Runtime] gsplat CUDA extension: OK")
    except Exception as exc:  # noqa: BLE001 - preserve compiler/import diagnostics
        logger.exception("[3DGS Runtime] gsplat CUDA extension load failed")
        raise RuntimeError(
            f"gsplat CUDA扩展加载失败：{type(exc).__name__}: {exc}"
        ) from exc


def normalize_exposure(
    images: list[np.ndarray],
) -> tuple[list[np.ndarray], dict]:
    """把各帧亮度/色偏对齐到中位亮度参考帧，抑制手机自动曝光波动。

    3DGS 假设朗伯表面与恒定光照；自动曝光使同一表面在不同视角出现颜色漂移，
    直接破坏外观拟合。仅做逐通道均值平移（不做对比度增益缩放），保持光度
    线性与纹理结构。返回 (对齐后图像, 诊断)。
    """
    if not images:
        return [], {"applied": False, "reason": "no_images"}
    arrays = [np.asarray(image, dtype=np.float32) / 255.0 for image in images]
    means = np.asarray([array.reshape(-1, 3).mean(axis=0) for array in arrays])
    brightness = means @ np.asarray([0.299, 0.587, 0.114])
    reference_index = int(np.argsort(np.abs(brightness - np.median(brightness)))[0])
    reference_means = means[reference_index]
    aligned = []
    max_shift = 0.0
    for array in arrays:
        shift = reference_means - array.reshape(-1, 3).mean(axis=0)
        max_shift = max(max_shift, float(np.abs(shift).max()))
        # 输出转回 uint8：下游 prepare_tensors / 语义检测仍按 0..255 约定消费图像。
        corrected = np.clip(array + shift, 0.0, 1.0)
        aligned.append((corrected * 255.0 + 0.5).astype(np.uint8))
    aligned_means = np.asarray(
        [array.reshape(-1, 3).mean(axis=0) for array in aligned], dtype=np.float64
    ) / 255.0
    diagnostics = {
        "applied": True,
        "reference_frame_index": reference_index,
        "brightness_before_min": float(brightness.min()),
        "brightness_before_max": float(brightness.max()),
        "brightness_after_min": float((aligned_means @ np.asarray([0.299, 0.587, 0.114])).min()),
        "brightness_after_max": float((aligned_means @ np.asarray([0.299, 0.587, 0.114])).max()),
        "max_channel_shift": float(max_shift),
    }
    logger.info(
        "exposure_normalization reference_frame=%d brightness_range=[%.3f,%.3f] -> [%.3f,%.3f]",
        reference_index,
        diagnostics["brightness_before_min"],
        diagnostics["brightness_before_max"],
        diagnostics["brightness_after_min"],
        diagnostics["brightness_after_max"],
    )
    return aligned, diagnostics


def filter_init_points(
    points: np.ndarray,
    colors: np.ndarray | None = None,
    *,
    nb_neighbors: int = 20,
    std_ratio: float = 2.0,
) -> tuple[np.ndarray, np.ndarray | None, dict]:
    """训练初始化前剔除点云统计离群点（飞点会变成 3DGS 漂浮高斯源头）。

    与 exporter.statistical_filter 同一参数约定（20 邻域、2σ）。点数过少时
    原样返回，避免把小场景清空。返回 (过滤后点, 同步过滤的颜色或 None, 诊断)。
    """
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    colors = (
        np.asarray(colors).reshape(-1, 3) if colors is not None and len(colors) else None
    )
    diagnostics = {"points_before": len(points), "points_after": len(points), "filtered": 0}
    skip = len(points) < 50
    if colors is not None and not skip:
        skip = len(colors) != len(points)
    if skip:
        return points, colors, diagnostics
    import open3d as o3d

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    filtered, indices = pcd.remove_statistical_outlier(nb_neighbors=nb_neighbors, std_ratio=std_ratio)
    kept = np.asarray(indices, dtype=int)
    diagnostics.update({
        "points_before": len(points),
        "points_after": len(kept),
        "filtered": len(points) - len(kept),
    })
    filtered_points = np.asarray(filtered.points, dtype=np.float64)
    filtered_colors = colors[kept] if colors is not None else None
    if diagnostics["filtered"]:
        logger.info(
            "init_point_cloud_filtered before=%d after=%d removed=%d (%.1f%%)",
            len(points),
            len(kept),
            diagnostics["filtered"],
            100.0 * diagnostics["filtered"] / len(points),
        )
    return filtered_points, filtered_colors, diagnostics


def normalize_scene(cameras: list[dict], points: np.ndarray) -> tuple[list[dict], np.ndarray, dict]:
    """把任意 SFM 尺度归一化到稳定训练范围，并返回可逆变换。"""
    centers = np.stack([np.asarray(c["center"], dtype=np.float64) for c in cameras])
    origin = np.median(centers, axis=0)
    radius = float(np.percentile(np.linalg.norm(centers - origin, axis=1), 90))
    if not np.isfinite(radius) or radius < 1e-6:
        radius = float(np.percentile(np.linalg.norm(points - np.median(points, axis=0), axis=1), 90))
    if not np.isfinite(radius) or radius < 1e-6:
        raise ValueError("SFM 场景尺度退化，无法训练")
    scale = 1.0 / radius
    normalized_points = (np.asarray(points, dtype=np.float64) - origin) * scale
    normalized_cameras = []
    for camera in cameras:
        item = dict(camera)
        R = np.asarray(camera["R"], dtype=np.float64)
        t = np.asarray(camera["t"], dtype=np.float64)
        item["t"] = scale * (R @ origin + t)
        item["center"] = (np.asarray(camera["center"], dtype=np.float64) - origin) * scale
        normalized_cameras.append(item)
    return normalized_cameras, normalized_points, {"origin": origin, "scale": scale}


def apply_scene_normalization(cameras: list[dict], transform: dict) -> list[dict]:
    """把独立 holdout 相机应用与训练集完全相同的场景归一化。"""
    origin = np.asarray(transform["origin"], dtype=np.float64)
    scale = float(transform["scale"])
    normalized = []
    for camera in cameras:
        item = dict(camera)
        rotation = np.asarray(camera["R"], dtype=np.float64)
        translation = np.asarray(camera["t"], dtype=np.float64)
        item["t"] = scale * (rotation @ origin + translation)
        item["center"] = (np.asarray(camera["center"], dtype=np.float64) - origin) * scale
        normalized.append(item)
    return normalized


def denormalize_gaussians(gaussians: dict, transform: dict) -> dict:
    """将训练结果恢复到原始 SFM 坐标系。"""
    result = dict(gaussians)
    scale = float(transform["scale"])
    origin = torch.as_tensor(transform["origin"], dtype=result["means"].dtype)
    result["means"] = result["means"] / scale + origin
    result["scales"] = result["scales"] - math.log(scale)
    return result


def prepare_tensors(cameras: list[dict], images: list[np.ndarray]) -> dict:
    """把位姿/图片转成训练张量。K(相机内参), c2w, imgs（均带 batch 维）。

    支持两种相机约定：
    - SFM 输出：含 "R"(world→cam) 与 "t" 键 → c2w = [[R.T, -R.T@t],[0,1]]
    - 合成相机（build_synthetic_cameras）：含 "R"(c2w 旋转) 与 "center" 键、无 "t" → c2w = [[R, center],[0,1]]
    """
    K = np.stack([c["K"] for c in cameras]).astype(np.float32)
    c2w = []
    for c in cameras:
        if "t" in c:
            R, t = c["R"].astype(np.float32), c["t"].astype(np.float32)
            c2w.append(np.block([[R.T, -R.T @ t.reshape(3, 1)], [0, 0, 0, 1]]))
        else:
            R, center = c["R"].astype(np.float32), np.asarray(c["center"], np.float32)
            c2w.append(np.block([[R, center.reshape(3, 1)], [0, 0, 0, 1]]))
    c2w = np.stack(c2w).astype(np.float32)
    imgs = np.stack([np.asarray(im)[..., :3].astype(np.float32) / 255.0 for im in images])
    return {
        "K": torch.from_numpy(K).to(DEVICE),
        "c2w": torch.from_numpy(c2w).to(DEVICE),
        "imgs": torch.from_numpy(imgs).to(DEVICE),
        "images_undistorted": all(bool(camera.get("undistorted", False)) for camera in cameras),
    }


def train_gaussians(
    gt: dict,
    init_points: np.ndarray | None = None,
    init_colors: np.ndarray | None = None,
    num_iter: int = NUM_ITER,
    *,
    profile: str = "official",
    seed: int = 42,
    validation_gt: dict | None = None,
    validation_dir: str | Path | None = None,
    evaluation_interval: int = 1000,
    early_stop_patience: int = 5,
) -> dict:
    """训练高斯场，返回 {means, scales, quats, opacities, sh0, sh_rest}（CPU 张量）。

    scales 返回 log 尺度（与 exporter 的 exp() 约定一致），opacities 返回 (N,)。
    """
    ensure_3dgs_runtime()
    from gsplat import DefaultStrategy, rasterization

    if profile not in {"official", "legacy"}:
        raise ValueError("profile 必须是 official 或 legacy")
    if evaluation_interval < 100:
        raise ValueError("evaluation_interval 不能小于 100")
    torch.manual_seed(seed)
    np.random.seed(seed)
    n = len(gt["imgs"])
    if init_points is None or len(init_points) < 100:
        init_points = np.random.randn(5000, 3).astype(np.float32)
        init_colors = np.full((len(init_points), 3), 0.5, dtype=np.float32)
    elif init_colors is None or len(init_colors) != len(init_points):
        init_colors = np.full((len(init_points), 3), 0.5, dtype=np.float32)
    init_colors = np.clip(np.asarray(init_colors, dtype=np.float32), 0.0, 1.0)
    means = torch.nn.Parameter(torch.from_numpy(init_points.astype(np.float32)).to(DEVICE))
    distances, _ = cKDTree(init_points).query(init_points, k=min(4, len(init_points)))
    nearest = np.maximum(np.mean(distances[:, 1:], axis=1), 1e-4)
    scales = torch.nn.Parameter(
        torch.log(torch.from_numpy(nearest.astype(np.float32))).to(DEVICE)[:, None].repeat(1, 3)
    )
    if profile == "official":
        quats = torch.nn.Parameter(F.normalize(torch.rand(len(means), 4, device=DEVICE), dim=-1))
    else:
        quats = torch.nn.Parameter(torch.zeros(len(means), 4, device=DEVICE))
        quats.data[:, 0] = 1.0
    opacities = torch.nn.Parameter(torch.full((len(means),), torch.logit(torch.tensor(0.1)).item(), device=DEVICE))
    rgb = torch.from_numpy(init_colors).to(DEVICE)
    sh0 = torch.nn.Parameter(((rgb - 0.5) / SH_C0)[:, None, :])
    sh_rest = torch.nn.Parameter(torch.zeros(len(means), (SH_DEGREE + 1) ** 2 - 1, 3, device=DEVICE))
    params = torch.nn.ParameterDict({
        "means": means,
        "scales": scales,
        "quats": quats,
        "opacities": opacities,
        "sh0": sh0,
        "sh_rest": sh_rest,
    })
    learning_rates = {
        "means": 1.6e-4,
        "scales": 5e-3,
        "quats": 1e-3,
        "opacities": 5e-2,
        "sh0": 2.5e-3,
        "sh_rest": 2.5e-3 / 20,
    }
    optimizers = {
        name: torch.optim.Adam(
            [{"params": [parameter], "lr": learning_rates[name], "name": name}],
            eps=1e-15,
        )
        for name, parameter in params.items()
    }
    means_decay = 0.01 ** (1.0 / max(num_iter, 1))
    means_scheduler = torch.optim.lr_scheduler.ExponentialLR(
        optimizers["means"], gamma=means_decay
    )
    strategy = None
    strategy_state = None
    if num_iter > 600:
        refine_stop = (
            max(601, num_iter - 100)
            if profile == "legacy"
            else min(10_000, max(601, num_iter - 100))
        )
        strategy = DefaultStrategy(refine_stop_iter=refine_stop)
        strategy.check_sanity(params, optimizers)
        strategy_state = strategy.initialize_state(scene_scale=1.0)

    H, W = gt["imgs"].shape[1], gt["imgs"].shape[2]
    quality_curve: list[dict] = []
    early_stop = ValidationEarlyStop(early_stop_patience)
    actual_iterations = num_iter
    # densification 结束后才允许提前停止，避免高斯仍在增殖时误判平台期。
    earliest_stop = min(num_iter, max(15_000, (strategy.refine_stop_iter + 5_000) if strategy else 15_000))
    for step in range(num_iter):
        idx = torch.randint(0, n, (1,), device=DEVICE)
        K, c2w, img = gt["K"][idx], gt["c2w"][idx], gt["imgs"][idx]
        colors = torch.cat([params["sh0"], params["sh_rest"]], dim=1)
        viewmats = torch.linalg.inv(c2w)
        render, _alpha, info = rasterization(
            params["means"], params["quats"], torch.exp(params["scales"]),
            torch.sigmoid(params["opacities"]), colors,
            viewmats, K, W, H, sh_degree=min(step // 1000, SH_DEGREE), packed=False,
        )
        if strategy is not None:
            strategy.step_pre_backward(params, optimizers, strategy_state, step, info)
        l1_loss = F.l1_loss(render, img)
        loss = l1_loss if profile == "legacy" else 0.8 * l1_loss + 0.2 * (1.0 - _ssim(render, img))
        for optimizer in optimizers.values():
            optimizer.zero_grad(set_to_none=True)
        loss.backward()
        for optimizer in optimizers.values():
            optimizer.step()
        means_scheduler.step()
        if strategy is not None:
            strategy.step_post_backward(
                params, optimizers, strategy_state, step, info, packed=False
            )
        if not torch.isfinite(loss):
            raise RuntimeError("3DGS 训练数值发散")
        completed = step + 1
        if validation_gt is not None and (
            completed % evaluation_interval == 0 or completed == num_iter
        ):
            checkpoint = _validate_views(
                params, validation_gt, rasterization, max_views=6, save_dir=None
            )
            current_psnr = checkpoint["validation_psnr_mean"]
            quality_curve.append({"iteration": completed, **checkpoint})
            # densification 期间 PSNR 会因 split/prune 短暂波动，不用于累计早停耐心值。
            should_stop = early_stop.update(
                current_psnr,
                ssim=checkpoint["validation_ssim_mean"],
                min_psnr=checkpoint["validation_psnr_min"],
                refinement_finished=strategy is None or completed > strategy.refine_stop_iter,
            )
            if completed >= earliest_stop and should_stop:
                actual_iterations = completed
                break
    validation_source = validation_gt if validation_gt is not None else gt
    validation = _validate_views(
        params,
        validation_source,
        rasterization,
        max_views=None if validation_gt is not None else 8,
        save_dir=validation_dir,
    )
    result = {name: value.detach().cpu() for name, value in params.items()}
    result["opacity_logits"] = True
    result["training_metrics"] = {
        "final_loss": float(loss.detach().cpu()),
        "iterations": actual_iterations,
        "configured_iterations": num_iter,
        "early_stopped": actual_iterations < num_iter,
        "still_improving_at_limit": (
            actual_iterations == num_iter and _quality_curve_still_improving(quality_curve)
        ),
        "training_view_count": n,
        "gaussian_count": len(result["means"]),
        "densification": strategy is not None,
        "training_profile": profile,
        "images_undistorted": bool(gt.get("images_undistorted", False)),
        "validation_is_holdout": validation_gt is not None,
        "quality_curve": quality_curve,
        **validation,
    }
    torch.cuda.empty_cache()
    return result


def prune_gaussians(
    gaussians: dict,
    reference_points: np.ndarray,
    *,
    opacity_threshold: float = 0.01,
    box_margin: float = 0.10,
    min_kept: int = 1000,
) -> tuple[dict, dict]:
    """训练后清理漂浮高斯：低透明度 + 超出参考点云主体包围盒的部分。

    参考点云取 1%-99% 分位包围盒并外扩 ``box_margin``；sigmoid(opacity) 低于
    ``opacity_threshold`` 的高斯对渲染几乎无贡献却会撑大包围盒/预览体积。
    剔除后若剩余数量不足 ``min_kept``，降级为只按透明度剔除，再不足则原样返回。
    """
    import torch

    reference = np.asarray(reference_points, dtype=np.float64).reshape(-1, 3)
    means_np = np.asarray(gaussians["means"].detach().cpu(), dtype=np.float64)
    opacities = torch.sigmoid(gaussians["opacities"].detach().cpu())
    visible = opacities.numpy() >= opacity_threshold
    diagnostics = {
        "gaussians_before": int(len(means_np)),
        "low_opacity_removed": int((~visible).sum()),
        "out_of_box_removed": 0,
        "gaussians_after": int(len(means_np)),
    }
    if len(reference) >= 10:
        lower = np.percentile(reference, 1, axis=0)
        upper = np.percentile(reference, 99, axis=0)
        extent = upper - lower
        lower -= box_margin * extent
        upper += box_margin * extent
        inside = np.all((means_np >= lower) & (means_np <= upper), axis=1)
    else:
        inside = np.ones(len(means_np), dtype=bool)
    keep = inside & visible
    if int(keep.sum()) < min_kept:
        keep = visible  # 包围盒误伤时降级：只按透明度剔除
    if int(keep.sum()) < min_kept:
        keep = np.ones(len(means_np), dtype=bool)  # 二次降级：原样保留
    diagnostics.update({
        "low_opacity_removed": int((~visible).sum()),
        "out_of_box_removed": int((~inside & visible).sum()),
        "gaussians_after": int(keep.sum()),
    })
    if int(keep.sum()) == len(means_np):
        return gaussians, diagnostics
    pruned = dict(gaussians)
    for key in ("means", "scales", "quats", "opacities", "sh0", "sh_rest"):
        if key in pruned:
            pruned[key] = pruned[key][keep]
    pruned["training_metrics"] = dict(gaussians.get("training_metrics", {}))
    pruned["training_metrics"]["gaussian_count"] = int(keep.sum())
    pruned["training_metrics"]["gaussian_pruning"] = diagnostics
    logger.info(
        "gaussian_pruning before=%d after=%d low_opacity=%d out_of_box=%d",
        len(means_np),
        int(keep.sum()),
        diagnostics["low_opacity_removed"],
        diagnostics["out_of_box_removed"],
    )
    return pruned, diagnostics


def _quality_curve_still_improving(curve: list[dict]) -> bool:
    """训练到上限时检查最后一个 1000 步区间是否仍有实质改善。"""
    if len(curve) < 2:
        return False
    previous, current = curve[-2], curve[-1]
    worst_safe = current["validation_psnr_min"] >= previous["validation_psnr_min"] - 0.05
    return worst_safe and (
        current["validation_psnr_mean"] > previous["validation_psnr_mean"] + 0.03
        or current["validation_ssim_mean"] > previous["validation_ssim_mean"] + 0.002
        or current["validation_psnr_min"] > previous["validation_psnr_min"] + 0.03
    )


def _ssim(prediction: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    """无额外依赖的可微 SSIM，输入为 NHWC、值域 0..1。"""
    x = prediction.permute(0, 3, 1, 2)
    y = target.permute(0, 3, 1, 2)
    kernel_size = min(11, int(x.shape[-2]), int(x.shape[-1]))
    if kernel_size % 2 == 0:
        kernel_size -= 1
    if kernel_size < 3:
        return 1.0 - F.l1_loss(x, y)
    padding = kernel_size // 2
    mu_x = F.avg_pool2d(x, kernel_size, stride=1, padding=padding)
    mu_y = F.avg_pool2d(y, kernel_size, stride=1, padding=padding)
    sigma_x = F.avg_pool2d(x * x, kernel_size, 1, padding) - mu_x.square()
    sigma_y = F.avg_pool2d(y * y, kernel_size, 1, padding) - mu_y.square()
    sigma_xy = F.avg_pool2d(x * y, kernel_size, 1, padding) - mu_x * mu_y
    c1, c2 = 0.01**2, 0.03**2
    score = ((2 * mu_x * mu_y + c1) * (2 * sigma_xy + c2)) / (
        (mu_x.square() + mu_y.square() + c1) * (sigma_x + sigma_y + c2)
    )
    return score.mean().clamp(0.0, 1.0)


@torch.inference_mode()
def _validate_views(params, gt: dict, rasterization, *, max_views: int | None, save_dir) -> dict:
    """渲染独立视角，计算 PSNR/SSIM，并保存最差视角三联图。"""
    count = int(gt["imgs"].shape[0])
    selected_count = count if max_views is None else min(count, max_views)
    indices = torch.linspace(0, count - 1, selected_count, device=DEVICE).round().long()
    psnr_values: list[float] = []
    ssim_values: list[float] = []
    coverage_values: list[float] = []
    rendered: list[tuple[int, np.ndarray, np.ndarray]] = []
    colors = torch.cat([params["sh0"], params["sh_rest"]], dim=1)
    height, width = int(gt["imgs"].shape[1]), int(gt["imgs"].shape[2])
    for index in indices:
        selected = index.reshape(1)
        render, alpha, _ = rasterization(
            params["means"], params["quats"], torch.exp(params["scales"]),
            torch.sigmoid(params["opacities"]), colors,
            torch.linalg.inv(gt["c2w"][selected]), gt["K"][selected],
            width, height, sh_degree=SH_DEGREE, packed=False,
        )
        mse = F.mse_loss(render, gt["imgs"][selected]).clamp_min(1e-12)
        psnr_values.append(float((-10.0 * torch.log10(mse)).cpu()))
        ssim_values.append(float(_ssim(render, gt["imgs"][selected]).cpu()))
        coverage_values.append(float((alpha > 0.05).float().mean().cpu()))
        if save_dir is not None:
            rendered.append((int(index), gt["imgs"][selected][0].cpu().numpy(), render[0].clamp(0, 1).cpu().numpy()))
    worst_files = []
    if save_dir is not None and rendered:
        import cv2
        output = Path(save_dir)
        output.mkdir(parents=True, exist_ok=True)
        for rank in np.argsort(psnr_values)[: min(5, len(rendered))]:
            source_index, truth, prediction = rendered[int(rank)]
            difference = np.abs(truth - prediction)
            triptych = np.concatenate([truth, prediction, difference], axis=1)
            filename = f"worst_{len(worst_files) + 1:02d}_view_{source_index:04d}.jpg"
            cv2.imwrite(str(output / filename), cv2.cvtColor((triptych * 255).astype(np.uint8), cv2.COLOR_RGB2BGR))
            worst_files.append(filename)
    return {
        "validation_view_count": len(psnr_values),
        "validation_psnr_mean": float(np.mean(psnr_values)),
        "validation_psnr_min": float(np.min(psnr_values)),
        "validation_ssim_mean": float(np.mean(ssim_values)),
        "validation_ssim_min": float(np.min(ssim_values)),
        "validation_alpha_coverage_min": float(np.min(coverage_values)),
        "validation_worst_view_files": worst_files,
    }

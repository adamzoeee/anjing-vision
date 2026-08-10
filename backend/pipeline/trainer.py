"""3DGS 训练：gsplat 1.5.x 光栅化 + Adam 优化，输入 SFM 相机位姿与图片。"""
import math
import importlib.util
import os
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from scipy.spatial import cKDTree

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
NUM_ITER = int(os.getenv("GAUSSIAN_TRAIN_ITERATIONS", "30000"))
SH_DEGREE = 3
SH_C0 = 0.28209479177387814
_DLL_HANDLES: list[object] = []


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
    """在读取全部训练图之前验证 GPU 与 gsplat 扩展，给出可操作的错误。"""
    if DEVICE != "cuda":
        raise RuntimeError("3DGS 训练需要 NVIDIA CUDA GPU，当前 PyTorch 未检测到 CUDA")
    # Windows 的 JIT 扩展需要能找到当前虚拟环境中的 ninja 和 CUDA nvcc。
    # 这里仅补齐当前进程 PATH，不写入系统环境，也不依赖任何个人绝对路径。
    path_entries = [str(Path(sys.executable).resolve().parent)]
    cuda_home = os.getenv("CUDA_HOME")
    if cuda_home:
        path_entries.append(str(Path(cuda_home) / "bin"))
    current_path = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join(path_entries + [current_path])
    try:
        _reuse_cached_gsplat_extension()
        from gsplat.cuda._backend import _C
        if _C is None:
            raise ImportError("gsplat CUDA extension was not loaded")
    except (AttributeError, ImportError, RuntimeError) as exc:
        raise RuntimeError(
            "gsplat CUDA 扩展不可用；请安装与 PyTorch 匹配的 CUDA Toolkit、"
            "Visual C++ Build Tools，并为中文用户目录配置纯英文 TEMP/TORCH_EXTENSIONS_DIR"
        ) from exc


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
) -> dict:
    """训练高斯场，返回 {means, scales, quats, opacities, sh0, sh_rest}（CPU 张量）。

    scales 返回 log 尺度（与 exporter 的 exp() 约定一致），opacities 返回 (N,)。
    """
    ensure_3dgs_runtime()
    from gsplat import DefaultStrategy, rasterization

    if profile not in {"official", "legacy"}:
        raise ValueError("profile 必须是 official 或 legacy")
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
            else min(15_000, max(601, num_iter - 100))
        )
        strategy = DefaultStrategy(refine_stop_iter=refine_stop)
        strategy.check_sanity(params, optimizers)
        strategy_state = strategy.initialize_state(scene_scale=1.0)

    H, W = gt["imgs"].shape[1], gt["imgs"].shape[2]
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
    validation = _validate_training_views(params, gt, rasterization)
    result = {name: value.detach().cpu() for name, value in params.items()}
    result["opacity_logits"] = True
    result["training_metrics"] = {
        "final_loss": float(loss.detach().cpu()),
        "iterations": num_iter,
        "gaussian_count": len(result["means"]),
        "densification": strategy is not None,
        "training_profile": profile,
        "images_undistorted": bool(gt.get("images_undistorted", False)),
        **validation,
    }
    torch.cuda.empty_cache()
    return result


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
def _validate_training_views(params, gt: dict, rasterization) -> dict:
    """用均匀分布的已注册视角检查最终模型，而不是只相信最后一次随机 loss。"""
    count = int(gt["imgs"].shape[0])
    indices = torch.linspace(0, count - 1, min(count, 8), device=DEVICE).round().long()
    psnr_values: list[float] = []
    coverage_values: list[float] = []
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
        coverage_values.append(float((alpha > 0.05).float().mean().cpu()))
    return {
        "validation_view_count": len(psnr_values),
        "validation_psnr_mean": float(np.mean(psnr_values)),
        "validation_psnr_min": float(np.min(psnr_values)),
        "validation_alpha_coverage_min": float(np.min(coverage_values)),
    }

"""视频抽帧：ffmpeg 时间候选帧 + 运动感知选择 + 清晰度连续性保护。"""
import logging
import math
import os
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger("anjing.pipeline")

TARGET_COUNT = 200       # 兼容既有调用接口；动态抽帧不按目标数量截断
FRAME_INTERVAL = 5       # 兼容旧代码/配置；动态抽帧不再使用固定帧间隔
MIN_VARIANCE = 100.0     # Laplacian 方差阈值（低于视为模糊；手机夜景/运动模糊下 60 过松）
SFM_FRAME_LIMIT = 250    # SfM 输入帧数上限：超出按时间均匀抽稀（单房间 250 帧足够，帧数翻倍只会拖慢并引入漂移）
CANDIDATE_MAX_FPS = 15.0
FAST_SAMPLE_FPS = 7.5
NORMAL_SAMPLE_FPS = 5.5
STATIC_SAMPLE_FPS = 2.0
MAX_SAMPLE_GAP_SECONDS = 0.5
MOTION_THUMBNAIL_SIZE = 320
MIN_TRACKED_FEATURES = 8
FAST_FLOW_RATIO = 0.012
NORMAL_FLOW_RATIO = 0.003
FAST_DIFFERENCE_SCORE = 0.08
NORMAL_DIFFERENCE_SCORE = 0.02
DEFAULT_MAX_DROPPED_RUN = 2

_FFMPEG_CANDIDATES = (
    # 本机 C:/Program Files/ffmpeg 下为 ffmpeg 4.0.2（不支持 -fps_mode），
    # 故将 C:/ffmpeg（ffmpeg 9.0）排在前面；仅按功能可用性排序，无其他语义。
    "C:/ffmpeg/bin/ffmpeg.exe",
    "C:/Program Files/ffmpeg/bin/ffmpeg.exe",
    "/usr/bin/ffmpeg",
)


def _ffmpeg_bin() -> str:
    """探测 ffmpeg 可执行文件：PATH 优先，其次常见安装路径。"""
    configured = os.getenv("FFMPEG_PATH")
    if configured:
        if os.path.isfile(configured):
            return configured
        raise FileNotFoundError(f"FFMPEG_PATH 指向的文件不存在: {configured}")
    p = shutil.which("ffmpeg")
    if p:
        return p
    for cand in _FFMPEG_CANDIDATES:
        if os.path.exists(cand):
            return cand
    raise FileNotFoundError("ffmpeg 未安装或不在 PATH")


def extract_frames(video: Path, out_dir: Path, target_count: int = TARGET_COUNT) -> list[Path]:
    """按时间生成候选帧，再根据相邻画面运动量动态保留关键帧。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("frame_*.jpg"):
        old.unlink()
    probe = subprocess.run(
        [_ffmpeg_bin(), "-i", str(video)],
        capture_output=True, text=True, errors="replace",
    )
    duration = 0.0
    for line in probe.stderr.splitlines():
        if "Duration:" in line:
            parts = line.split("Duration:")[1].split(",")[0].strip().split(":")
            duration = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            break
    if duration <= 0:
        raise ValueError(f"无法解析视频时长: {video}")
    # target_count 保留在函数签名中以兼容现有调用；动态抽帧不按总数截断。
    _ = target_count
    capture = cv2.VideoCapture(str(video))
    source_fps = float(capture.get(cv2.CAP_PROP_FPS)) if capture.isOpened() else 0.0
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) if capture.isOpened() else 0
    capture.release()
    candidate_fps = min(source_fps, CANDIDATE_MAX_FPS) if source_fps > 0 else CANDIDATE_MAX_FPS
    # 浮点 PTS 可能略小于精确的 1/fps；保留 10% 容差，避免 30 FPS 输入
    # 因 0.066666... 的舍入误差退化为约 10 FPS。select 仍只取真实输入帧。
    candidate_interval = 0.9 / max(candidate_fps, 1e-6)
    # select 使用时间戳而非原始帧编号；只选择已有输入帧，不会给低 FPS/VFR 视频补重复帧。
    select_filter = (
        "select='isnan(prev_selected_t)+"
        f"gte(t-prev_selected_t\\,{candidate_interval:.9f})'"
    )
    subprocess.run([
        _ffmpeg_bin(), "-y", "-i", str(video),
        # 只缩小不放大：低分辨率输入放大后插值模糊，会误伤清晰度过滤
        "-vf", f"{select_filter},scale='min(1600,iw)':-2",
        "-fps_mode", "vfr",
        "-q:v", "2", str(out_dir / "frame_%05d.jpg"),
    ], check=True, capture_output=True)
    candidate_paths = sorted(out_dir.glob("frame_*.jpg"))
    paths, motion_stats = _select_dynamic_frames(candidate_paths, candidate_fps)
    selected = set(paths)
    for path in candidate_paths:
        if path not in selected:
            path.unlink()
    logger.info(
        "video_sampling source_fps=%.3f total_frames=%d candidate_fps=%.3f "
        "decoded_candidate_frames=%d candidate_frames=%d fast_motion_frames=%d "
        "normal_motion_frames=%d static_frames=%d max_sample_gap_seconds=%.3f",
        source_fps,
        total_frames,
        candidate_fps,
        len(candidate_paths),
        len(paths),
        motion_stats["fast_motion_frames"],
        motion_stats["normal_motion_frames"],
        motion_stats["static_frames"],
        motion_stats["max_sample_gap_seconds"],
    )
    return paths


def _select_dynamic_frames(
    candidate_paths: list[Path], candidate_fps: float,
) -> tuple[list[Path], dict]:
    """根据相邻候选帧的光流/灰度变化选择关键帧，并限制最大时间空洞。"""
    if not candidate_paths:
        return [], _motion_stats()
    candidate_fps = max(float(candidate_fps), 1.0)
    fast_steps = max(1, math.ceil(candidate_fps / FAST_SAMPLE_FPS))
    normal_steps = max(1, math.ceil(candidate_fps / NORMAL_SAMPLE_FPS))
    static_steps = max(1, math.ceil(candidate_fps / STATIC_SAMPLE_FPS))
    max_gap_steps = max(1, math.floor(candidate_fps * MAX_SAMPLE_GAP_SECONDS))
    static_steps = min(static_steps, max_gap_steps)

    selected = [candidate_paths[0]]
    previous = _read_motion_gray(candidate_paths[0])
    last_selected_index = 0
    stats = _motion_stats()
    for index, path in enumerate(candidate_paths[1:], start=1):
        current = _read_motion_gray(path)
        score, method = _motion_score(previous, current)
        previous = current
        if _is_fast_motion(score, method):
            target_steps = fast_steps
            stats["fast_motion_frames"] += 1
        elif _is_normal_motion(score, method):
            target_steps = normal_steps
            stats["normal_motion_frames"] += 1
        else:
            target_steps = static_steps
            stats["static_frames"] += 1
        elapsed_steps = index - last_selected_index
        if elapsed_steps >= min(target_steps, max_gap_steps):
            selected.append(path)
            last_selected_index = index

    if len(candidate_paths) - 1 - last_selected_index >= max_gap_steps:
        selected.append(candidate_paths[-1])
    selected_indices = [candidate_paths.index(path) for path in selected]
    stats["max_sample_gap_seconds"] = (
        max(np.diff(selected_indices), default=0) / candidate_fps
    )
    return selected, stats


def _motion_stats() -> dict:
    return {
        "fast_motion_frames": 0,
        "normal_motion_frames": 0,
        "static_frames": 0,
        "max_sample_gap_seconds": 0.0,
    }


def _read_motion_gray(path: Path) -> np.ndarray | None:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    height, width = image.shape[:2]
    scale = min(1.0, MOTION_THUMBNAIL_SIZE / max(height, width))
    if scale < 1.0:
        image = cv2.resize(
            image,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    return image


def _motion_score(previous: np.ndarray | None, current: np.ndarray | None) -> tuple[float, str]:
    """优先返回归一化稀疏光流；特征不足时返回归一化灰度差异。"""
    if previous is None or current is None or previous.shape != current.shape:
        return 1.0, "difference"
    features = cv2.goodFeaturesToTrack(
        previous,
        maxCorners=160,
        qualityLevel=0.01,
        minDistance=7,
        blockSize=7,
    )
    if features is not None and len(features) >= MIN_TRACKED_FEATURES:
        tracked, status, _ = cv2.calcOpticalFlowPyrLK(
            previous,
            current,
            features,
            None,
            winSize=(21, 21),
            maxLevel=3,
        )
        if tracked is not None and status is not None:
            valid = status.reshape(-1).astype(bool)
            if int(valid.sum()) >= MIN_TRACKED_FEATURES:
                displacement = np.linalg.norm(
                    tracked.reshape(-1, 2)[valid] - features.reshape(-1, 2)[valid],
                    axis=1,
                )
                diagonal = math.hypot(*previous.shape[:2])
                return float(np.median(displacement) / max(diagonal, 1.0)), "flow"
    difference = cv2.absdiff(previous, current)
    return float(np.mean(difference) / 255.0), "difference"


def _is_fast_motion(score: float, method: str) -> bool:
    return score >= (FAST_FLOW_RATIO if method == "flow" else FAST_DIFFERENCE_SCORE)


def _is_normal_motion(score: float, method: str) -> bool:
    return score >= (NORMAL_FLOW_RATIO if method == "flow" else NORMAL_DIFFERENCE_SCORE)


def filter_sharp_frames(
    frame_paths: list[Path], min_variance: float = MIN_VARIANCE,
) -> tuple[list[Path], list[Path]]:
    """按 Laplacian 方差过滤模糊帧。返回 (保留, 丢弃)。"""
    kept, dropped = [], []
    for p in frame_paths:
        variance = _laplacian_variance(p)
        if variance is None:
            dropped.append(p)
            continue
        (kept if variance >= min_variance else dropped).append(p)
    return kept, dropped


def protect_sfm_continuity(
    frame_paths: list[Path],
    sharp_frames: list[Path],
    max_dropped_run: int = DEFAULT_MAX_DROPPED_RUN,
) -> tuple[list[Path], list[Path], dict]:
    """恢复长模糊区间中相对最清晰的可读帧，仅供 SfM 保持轨迹连续。"""
    if max_dropped_run < 0:
        raise ValueError("max_dropped_run 必须大于等于 0")
    ordered = list(frame_paths)
    sharp = set(sharp_frames) & set(ordered)
    sfm = set(sharp)
    scores = {path: _laplacian_variance(path) for path in ordered if path not in sharp}
    before = _max_dropped_run(ordered, sfm)
    recovered: set[Path] = set()

    while True:
        excessive = [run for run in _dropped_runs(ordered, sfm) if len(run) > max_dropped_run]
        if not excessive:
            break
        changed = False
        for run in excessive:
            readable = [path for path in run if scores.get(path) is not None]
            if not readable:
                continue
            bridge = max(readable, key=lambda path: float(scores[path]))
            sfm.add(bridge)
            recovered.add(bridge)
            changed = True
        if not changed:
            break

    sfm_frames = [path for path in ordered if path in sfm]
    recovered_frames = [path for path in ordered if path in recovered]
    after = _max_dropped_run(ordered, sfm)
    stats = {
        "candidate_frames": len(ordered),
        "sharp_frames": len(sharp),
        "blurred_frames": len(ordered) - len(sharp),
        "sfm_bridge_frames_restored": len(recovered_frames),
        "sfm_input_frames": len(sfm_frames),
        "max_dropped_run_before_recovery": before,
        "max_dropped_run_after_recovery": after,
    }
    logger.info(
        "sfm_continuity candidate_frames=%d sharp_frames=%d blurred_frames=%d "
        "sfm_bridge_frames_restored=%d sfm_input_frames=%d "
        "max_dropped_run_before_recovery=%d max_dropped_run_after_recovery=%d",
        *stats.values(),
    )
    return sfm_frames, recovered_frames, stats


def _laplacian_variance(path: Path) -> float | None:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    return float(cv2.Laplacian(image, cv2.CV_64F).var())


def _dropped_runs(ordered: list[Path], kept: set[Path]) -> list[list[Path]]:
    runs: list[list[Path]] = []
    current: list[Path] = []
    for path in ordered:
        if path in kept:
            if current:
                runs.append(current)
                current = []
        else:
            current.append(path)
    if current:
        runs.append(current)
    return runs


def _max_dropped_run(ordered: list[Path], kept: set[Path]) -> int:
    return max((len(run) for run in _dropped_runs(ordered, kept)), default=0)


def decimate_frames(paths: list[Path], limit: int = SFM_FRAME_LIMIT) -> list[Path]:
    """把按时间排序的帧均匀抽稀到 ``limit`` 张以内，始终保留首尾帧。

    边走边拍的长视频按运动量抽帧后仍可能得到 600+ 帧；单房间 SfM 只需
    约 200~250 帧。帧数过多时顺序匹配与增量重建开销接近线性甚至超线性，
    且过小的相邻基线只会放大漂移。均匀抽稀保持时间等距，不破坏顺序匹配
    的连续性假设。
    """
    ordered = list(paths)
    if len(ordered) <= limit:
        return ordered
    limit = max(2, int(limit))
    indices: set[int] = {0, len(ordered) - 1}
    step = (len(ordered) - 1) / (limit - 1)
    indices.update(round(i * step) for i in range(1, limit - 1))
    return [ordered[index] for index in sorted(indices)]

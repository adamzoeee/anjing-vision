"""vid2scene 适配层单元测试：命令构造、进度映射、COLMAP 模型与 splat.ply 解析。"""
from pathlib import Path

import numpy as np
import pytest

import pipeline.vid2scene_runner as vr


@pytest.fixture(autouse=True)
def _clear_env_cache():
    vr.vid2scene_env_python.cache_clear()
    yield
    vr.vid2scene_env_python.cache_clear()


def _write_synthetic_colmap_model(sparse_dir: Path) -> None:
    """手写最小 COLMAP 稀疏模型（2 相机 2 图 5 点）供解析测试。

    pycolmap 4.1 的 rig 新 API 不再支持给独立 Image 直接设位姿，
    直接写二进制格式反而更贴近真实产物（vid2scene 的 hloc/glomap 输出）。
    """
    import struct

    sparse_dir.mkdir(parents=True, exist_ok=True)

    # cameras.bin：2 台 SIMPLE_RADIAL（model_id=2），fx=600, cx=320, cy=240, k=0.05
    # pycolmap 4.x 布局：camera_id(i32), model_id(i32), width(u64), height(u64),
    # 随后直接是参数（个数由模型决定，不再有 num_params 字段）。
    with open(sparse_dir / "cameras.bin", "wb") as handle:
        handle.write(struct.pack("<Q", 2))
        for camera_id in (1, 2):
            handle.write(struct.pack("<iiQQ", camera_id, 2, 640, 480))
            handle.write(struct.pack("<4d", 600.0, 320.0, 240.0, 0.05))

    # images.bin：2 张图，位姿 = 平移 [i,0,0]；每图 5 个 points2D 对应 5 个点
    with open(sparse_dir / "images.bin", "wb") as handle:
        handle.write(struct.pack("<Q", 2))
        for image_id, camera_id in ((1, 1), (2, 2)):
            tx = float(image_id - 1)
            handle.write(struct.pack("<I", image_id))
            handle.write(struct.pack("<4d", 1.0, 0.0, 0.0, 0.0))  # qw,qx,qy,qz
            handle.write(struct.pack("<3d", tx, 0.0, 0.0))
            handle.write(struct.pack("<I", camera_id))
            name = f"image_{image_id:04d}.png\0".encode("utf-8")
            handle.write(name)
            handle.write(struct.pack("<Q", 5))
            for point_id in range(1, 6):
                handle.write(struct.pack("<ddq", 100.0 + point_id, 100.0, point_id))

    # points3D.bin：5 个点，track 引用各图的 points2D[pid-1]
    with open(sparse_dir / "points3D.bin", "wb") as handle:
        handle.write(struct.pack("<Q", 5))
        for pid in range(1, 6):
            handle.write(struct.pack("<Q", pid))
            handle.write(struct.pack("<3d", float(pid), 1.0, 2.0))
            handle.write(struct.pack("<3B", 10, 20, 30))
            handle.write(struct.pack("<d", 0.5))
            handle.write(struct.pack("<Q", 2))
            for image_id in (1, 2):
                handle.write(struct.pack("<II", image_id, pid - 1))


def _write_synthetic_splat_ply(ply_path: Path, count: int = 8) -> None:
    """写一个最小 3DGS PLY（与 vid2scene/我们的导出布局一致）。"""
    names = ["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2"]
    names += [f"f_rest_{i}" for i in range(45)]
    names += ["opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    rng = np.random.default_rng(3)
    dtype = np.dtype([(n, "<f4") for n in names])
    data = np.zeros(count, dtype=dtype)
    data["x"] = rng.uniform(-1, 1, count)
    data["y"] = rng.uniform(-1, 1, count)
    data["z"] = rng.uniform(-1, 1, count)
    data["f_dc_0"] = 1.0
    data["f_dc_1"] = 0.5
    data["f_dc_2"] = 0.0
    data["opacity"] = np.array([3.0] * (count // 2) + [-6.0] * (count - count // 2), dtype="<f4")
    data["scale_0"] = 0.01
    data["scale_1"] = 0.01
    data["scale_2"] = 0.01
    data["rot_0"] = 1.0
    ply_path.parent.mkdir(parents=True, exist_ok=True)
    header = ["ply", "format binary_little_endian 1.0", "comment test"]
    header.append(f"element vertex {count}")
    header += [f"property float {n}" for n in names]
    header.append("end_header")
    with open(ply_path, "wb") as handle:
        handle.write(("\n".join(header) + "\n").encode("ascii"))
        data.tofile(handle)


def test_build_command_includes_no_normalize_world_space(monkeypatch):
    monkeypatch.setenv("VID2SCENE_PYTHON", r"C:\fake\vid2scene\python.exe")
    monkeypatch.setattr(vr, "VID2SCENE_CORE_DIR", Path(r"C:\fake\vid2scene_core"))
    command = vr.build_command(
        Path(r"C:\video.mp4"), Path(r"C:\work"),
        target_framecount=300, training_num_steps=20000,
        max_gaussians=1200000, reconstruction_method="colmap",
    )
    assert "--no_normalize_world_space" in command
    assert "--training_max_num_gaussians" in command
    assert "1200000" in command
    assert command[command.index("--reconstruction_method") + 1] == "colmap"
    assert command[command.index("--apriltag_size") + 1] == "0.09"


def test_build_command_omits_apriltag_when_disabled(monkeypatch):
    monkeypatch.setenv("VID2SCENE_PYTHON", r"C:\fake\vid2scene\python.exe")
    command = vr.build_command(
        Path(r"C:\video.mp4"), Path(r"C:\work"), apriltag_enabled=False
    )
    assert "--apriltag_size" not in command


def test_build_command_uses_image_dir_for_photo_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("VID2SCENE_PYTHON", r"C:\fake\vid2scene\python.exe")
    source = tmp_path / "photos"
    source.mkdir()
    command = vr.build_command(source, tmp_path / "work")
    assert command[command.index("--image_dir") + 1] == str(source)
    assert "--video_path" not in command


def test_map_progress_stage_markers():
    assert vr.map_progress("Extracting frames from video...", 20000) == 0.05
    assert vr.map_progress("Doing matches", 20000) == 0.40
    assert vr.map_progress("Running Gsplat script", 20000) == 0.55
    assert vr.map_progress("step=5000 loss=0.1", 20000) == pytest.approx(0.6575)
    assert vr.map_progress("随便一行日志", 20000) is None


def test_parse_sparse_model_reads_cameras_and_points(tmp_path):
    sparse_dir = tmp_path / "sparse" / "0"
    _write_synthetic_colmap_model(sparse_dir)
    result = vr.parse_sparse_model(sparse_dir)
    assert len(result["cameras"]) == 2
    assert result["points3D"].shape == (5, 3)
    assert result["colors3D"].shape == (5, 3)
    camera = sorted(result["cameras"], key=lambda item: item["name"])[0]
    assert camera["camera_model"] == "SIMPLE_RADIAL"
    assert np.allclose(camera["K"], np.array([[600, 0, 320], [0, 600, 240], [0, 0, 1.0]]))
    assert np.allclose(camera["center"], np.array([0.0, 0.0, 0.0]), atol=1e-6)
    quality = result["quality"]
    assert quality["registered_images"] == 2
    assert quality["points3D"] == 5
    assert quality["backend"] == "vid2scene"


def test_read_splat_ply_parses_gaussian_layout(tmp_path):
    ply_path = tmp_path / "ply" / "splat.ply"
    _write_synthetic_splat_ply(ply_path)
    splat = vr.read_splat_ply(ply_path)
    assert splat["means"].shape == (8, 3)
    assert splat["colors"].shape == (8, 3)
    assert splat["scales"].shape == (8, 3)
    assert splat["quats"].shape == (8, 4)
    assert splat["sh_rest"].shape == (8, 45)
    # logit 3.0 → sigmoid ≈ 0.95；logit -6 → ≈ 0.0025
    assert splat["opacities"][0] > 0.9
    assert splat["opacities"][-1] < 0.01


def test_point_cloud_from_splat_filters_by_opacity(tmp_path):
    ply_path = tmp_path / "splat.ply"
    _write_synthetic_splat_ply(ply_path)
    splat = vr.read_splat_ply(ply_path)
    points, colors = vr.point_cloud_from_splat(splat, opacity_threshold=0.5)
    assert len(points) == 4
    assert colors.shape == (4, 3)


def test_run_reconstruction_with_stub_pipeline(tmp_path, monkeypatch):
    """用桩脚本验证 run_reconstruction 的子进程编排与进度回调。"""
    work = tmp_path / "work"
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")

    core_dir = tmp_path / "core"
    core_dir.mkdir()
    stub = (
        "import sys, pathlib\n"
        f"out = pathlib.Path(sys.argv[1])\n"
        "(out / 'sfm_output' / 'sparse' / '0').mkdir(parents=True, exist_ok=True)\n"
        "(out / 'ply').mkdir(parents=True, exist_ok=True)\n"
        "(out / 'results' / 'stats').mkdir(parents=True, exist_ok=True)\n"
        "(out / 'ply' / 'splat.ply').write_bytes(b'x')\n"
        "print('Extracting frames from video')\n"
        "print('Doing retrieval')\n"
        "print('Running Gsplat script')\n"
        "print('Applied scale factor: 0.1234')\n"
        "print('step=1000 loss=0.2')\n"
    )
    (core_dir / "vid2scene.py").write_text(stub, encoding="utf-8")
    monkeypatch.setattr(vr, "VID2SCENE_CORE_DIR", core_dir)
    monkeypatch.setenv("VID2SCENE_PYTHON", str(Path(__import__("sys").executable)))

    seen: list[float] = []
    result = vr.run_reconstruction(
        video, work, progress_callback=lambda p: seen.append(p), training_num_steps=20000
    )
    assert result["splat_ply"] == work / "ply" / "splat.ply"
    assert result["metric_calibration"]["status"] == "metric_apriltag"
    assert result["metric_calibration"]["scale_applied_by"] == "vid2scene"
    assert seen and seen[-1] >= 0.55  # gsplat 阶段已进入
    assert seen == sorted(seen)  # 进度单调不减（本桩输出顺序保证）


def test_run_reconstruction_failure_raises_with_log_tail(tmp_path, monkeypatch):
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "vid2scene.py").write_text(
        "import sys\nprint('boom: something broke')\nsys.exit(3)\n", encoding="utf-8"
    )
    monkeypatch.setattr(vr, "VID2SCENE_CORE_DIR", core_dir)
    monkeypatch.setenv("VID2SCENE_PYTHON", str(Path(__import__("sys").executable)))
    with pytest.raises(RuntimeError, match="退出码 3"):
        vr.run_reconstruction(tmp_path / "video.mp4", tmp_path / "work")


def test_run_reconstruction_rejects_missing_apriltag_calibration(tmp_path, monkeypatch):
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "vid2scene.py").write_text("print('finished without tag')\n", encoding="utf-8")
    monkeypatch.setattr(vr, "VID2SCENE_CORE_DIR", core_dir)
    monkeypatch.setenv("VID2SCENE_PYTHON", str(Path(__import__("sys").executable)))
    with pytest.raises(RuntimeError, match="尺度标定失败"):
        vr.run_reconstruction(tmp_path / "video.mp4", tmp_path / "work")
    metadata = __import__("json").loads(
        (tmp_path / "work" / "metric_calibration.json").read_text(encoding="utf-8")
    )
    assert metadata["status"] == "calibration_failed"
    assert metadata["coordinate_unit"] == "model_units"


def test_apriltag_scale_pattern_matches_both_engine_outputs():
    # 引擎 print 版（apriltag_calibration.py）
    match = vr._APRILTAG_SCALE_PATTERN.search("Scale factor: 1.2345 (from 1 tag)")
    assert match is not None and float(match.group(1)) == 1.2345
    # 引擎 logger 版（vid2scene.py）
    match = vr._APRILTAG_SCALE_PATTERN.search("INFO:__main__:✓ Applied scale factor: 2.3456")
    assert match is not None and float(match.group(1)) == 2.3456


def test_run_reconstruction_accepts_print_style_scale_output(tmp_path, monkeypatch):
    """引擎只打印 print 版 "Scale factor:" 时，标定必须判定成功而非误报失败。"""
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "vid2scene.py").write_text(
        "print('Triangulating AprilTag corners...')\n"
        "print('Scale factor: 1.2345 (from 1 tag)')\n"
        "print('Rescaling reconstruction by factor 1.2345...')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(vr, "VID2SCENE_CORE_DIR", core_dir)
    monkeypatch.setenv("VID2SCENE_PYTHON", str(Path(__import__("sys").executable)))
    outputs = vr.run_reconstruction(tmp_path / "video.mp4", tmp_path / "work")
    calibration = __import__("json").loads(
        (tmp_path / "work" / "metric_calibration.json").read_text(encoding="utf-8")
    )
    assert calibration["status"] == "metric_apriltag"
    assert calibration["coordinate_unit"] == "meters"
    assert calibration["scale_factor"] == 1.2345
    assert outputs["metric_calibration"]["scale_factor"] == 1.2345


def test_export_gaussian_ply_preserves_log_scale(tmp_path):
    """渲染器会对 scale 做 exp；导出必须直通 log 尺度。

    双重 log 会让渲染尺寸趋近于 0，画面全黑（真实验收事故），本测试锁定。
    """
    import torch

    from pipeline.exporter import export_gaussian_ply

    gaussians = {
        "means": torch.zeros(3, 3),
        "scales": torch.full((3, 3), -4.8),  # vid2scene 输出：已是 log 尺度
        "quats": torch.zeros(3, 4),
        "opacities": torch.zeros(3, 1),
        "sh0": torch.zeros(3, 1, 3),
        "sh_rest": torch.zeros(3, 15, 3),
        "opacity_logits": True,
    }
    out = tmp_path / "g.ply"
    export_gaussian_ply(gaussians, out)
    data, _names = vr._read_binary_ply(out)
    assert np.allclose(data["scale_0"], -4.8)
    assert np.allclose(data["scale_1"], -4.8)
    assert np.allclose(data["scale_2"], -4.8)


def test_parse_reconstruction_exposes_relative_scale_contract(tmp_path, monkeypatch):
    work = tmp_path / "work"
    monkeypatch.setattr(vr, "parse_sparse_model", lambda _path: {
        "cameras": [], "points3D": np.empty((0, 3)), "colors3D": np.empty((0, 3)),
        "quality": {},
    })
    monkeypatch.setattr(vr, "read_splat_ply", lambda _path: {"means": np.empty((0, 3))})
    (work / "ply").mkdir(parents=True)
    (work / "ply" / "splat.ply").write_bytes(b"x")
    result = vr.parse_reconstruction(work)
    assert result["metric_scale_status"] == "relative"
    assert result["coordinate_unit"] == "model_units"

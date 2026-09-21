"""Build the fourth scan-46 handoff bundle without duplicating the first three bundles."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import zipfile
import zlib


IGNORED_NAMES = {"__pycache__", ".pytest_cache"}
IGNORED_SUFFIXES = {".pyc", ".log", ".tmp"}

FOCUSED_FILES = (
    Path("data/work/46/postprocess/preview_selection.json"),
    Path("data/work/46/postprocess/local_gaps_20260912.ply"),
    Path("data/work/46/postprocess/local_gaps_20260912.visual.json"),
)
FOCUSED_DIRS = (
    Path("data/work/46/local_gaps_20260912/new2/window2"),
    Path("data/work/46/local_gaps_20260912/new2/bed2"),
    Path("data/work/46/local_gaps_20260912/new2/ownership"),
    Path("data/work/46/local_gaps_20260912/viewer_diagnostic"),
    Path("data/work/46/local_gaps_20260912/rejection_audit"),
    Path("data/work/46/local_gaps_20260912/independent_objects_review"),
    Path("data/work/46/local_gaps_20260912/independent_objects_verified"),
)
TARGET_R2_EXCLUDED = {"map_localization", "candidate.ply"}


def normalize_archive_name(name: str) -> str:
    return name.replace("\\", "/").lstrip("./")


def load_overlay_state(archives: list[Path]) -> dict[str, tuple[int, int, str]]:
    """Return the final path/size/CRC state after archives are applied in order."""
    state: dict[str, tuple[int, int, str]] = {}
    for archive in archives:
        with zipfile.ZipFile(archive) as handle:
            for item in handle.infolist():
                name = normalize_archive_name(item.filename)
                if item.is_dir() or not name.startswith("data/"):
                    continue
                state[name] = (item.file_size, item.CRC, archive.name)
    return state


def file_crc32(path: Path) -> int:
    value = 0
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            value = zlib.crc32(chunk, value)
    return value & 0xFFFFFFFF


def should_ignore(path: Path) -> bool:
    return bool(IGNORED_NAMES.intersection(path.parts)) or path.suffix.lower() in IGNORED_SUFFIXES


def iter_data_files(backend_root: Path):
    emitted: set[Path] = set()
    for relative in FOCUSED_FILES:
        path = backend_root / relative
        if path.is_file():
            emitted.add(path)
            yield path, relative.as_posix()
    for relative_root in FOCUSED_DIRS:
        root = backend_root / relative_root
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path not in emitted and not should_ignore(path):
                emitted.add(path)
                yield path, normalize_archive_name(str(path.relative_to(backend_root)))
    gaps_root = backend_root / "data/work/46/local_gaps_20260912"
    if gaps_root.is_dir():
        for path in sorted(gaps_root.iterdir()):
            if path.is_file() and path.suffix.lower() != ".ply" and path not in emitted and not should_ignore(path):
                emitted.add(path)
                yield path, normalize_archive_name(str(path.relative_to(backend_root)))
    target_root = gaps_root / "target_r2_20260914"
    if target_root.is_dir():
        for path in sorted(target_root.rglob("*")):
            relative = path.relative_to(target_root)
            if relative.parts and relative.parts[0] in TARGET_R2_EXCLUDED:
                continue
            if path.is_file() and path not in emitted and not should_ignore(path):
                emitted.add(path)
                yield path, normalize_archive_name(str(path.relative_to(backend_root)))


def iter_source_videos(source_root: Path):
    for path in sorted(source_root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(source_root).as_posix()
            yield path, f"data/training_sources/scan46_buchong2/{relative}"


def select_delta(
    backend_root: Path,
    source_root: Path,
    overlay_state: dict[str, tuple[int, int, str]],
) -> tuple[list[tuple[Path, str, int]], list[dict]]:
    selected: list[tuple[Path, str, int]] = []
    skipped: list[dict] = []
    for path, archive_name in [*iter_data_files(backend_root), *iter_source_videos(source_root)]:
        size = path.stat().st_size
        previous = overlay_state.get(archive_name)
        crc = file_crc32(path)
        if previous and previous[:2] == (size, crc):
            skipped.append({"path": archive_name, "provided_by": previous[2], "bytes": size})
            continue
        selected.append((path, archive_name, crc))
    return selected, skipped


def build_bundle(
    backend_root: Path,
    source_root: Path,
    archives: list[Path],
    output: Path,
) -> dict:
    for path in [backend_root, source_root, *archives]:
        if not path.exists():
            raise FileNotFoundError(path)
    overlay_state = load_overlay_state(archives)
    selected, skipped = select_delta(backend_root, source_root, overlay_state)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial")
    if partial.exists():
        partial.unlink()
    generated = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest = {
        "schema": 1,
        "generated_at_utc": generated,
        "purpose": "扫描46窗边点云研究交接；不得作为尺寸、结构或风险评估的替代数据",
        "apply_after": [archive.name for archive in archives],
        "entries": [
            {"path": name, "bytes": path.stat().st_size, "crc32": f"{crc:08x}"}
            for path, name, crc in selected
        ],
        "skipped_identical_entries": skipped,
    }
    readme = """# 扫描46训练与补拍研究增量包

本包是第四个重点包，只包含前三个包没有提供或内容已经变化的扫描46窗户研究数据，包含 `buchong2` 补拍视频、当前源机预览选择、R2/窗边核心重建和真实网页诊断。失败的全局定位、重复点图和无效贴片候选未打包。

必须先依次应用：

1. scan46_完整数据包.zip
2. scan46_最新结果补充包.zip
3. scan46_窗户增量补充包.zip
4. 本包

推荐直接使用 GitHub `main` 中的 `backend/scripts/deploy_scan46.ps1 -ResearchBundlePath ...`。本包中的实验候选用于继续修复窗边黑糊问题；结构、尺寸和风险计算仍以正式数据为准。

## 窗户视频优先级

1. `data/training_sources/scan46_buchong2/窗户边角2.mp4`：窗帘下缘、靠垫、袋子、右侧黑区，第一优先。
2. `data/training_sources/scan46_buchong2/窗户边角.mp4`：窗边角落和黑区归属。
3. `data/training_sources/scan46_buchong2/窗户那面墙/1000045072.mp4`
4. `data/training_sources/scan46_buchong2/窗户那面墙/窗户那面.mp4`

后两段主要提供窗框、窗台和墙面刚性参照。不同视频里的窗帘形态可能变化，禁止把不同时间的窗帘褶皱直接刚性融合。
"""
    with zipfile.ZipFile(
        partial,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=1,
        allowZip64=True,
    ) as target:
        for index, (path, name, _) in enumerate(selected, 1):
            target.write(path, name)
            if index % 250 == 0:
                print(f"packed {index}/{len(selected)}", flush=True)
        target.writestr("scan46_research_bundle_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        target.writestr("README_扫描46训练与补拍研究增量包.md", readme)
    os.replace(partial, output)
    manifest["archive"] = str(output)
    manifest["archive_bytes"] = output.stat().st_size
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-root", type=Path, required=True)
    parser.add_argument("--source-videos", type=Path, required=True)
    parser.add_argument("--baseline-archive", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path)
    args = parser.parse_args()
    result = build_bundle(
        args.backend_root.resolve(),
        args.source_videos.resolve(),
        [path.resolve() for path in args.baseline_archive],
        args.output.resolve(),
    )
    if args.manifest_output:
        args.manifest_output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"archive": result["archive"], "bytes": result["archive_bytes"], "entries": len(result["entries"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()

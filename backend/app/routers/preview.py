"""3D 预览：查看器页面 + 场景清单 + 点云/结构文件服务。

查看器是静态页面（backend/app/static/preview），通过 ?scan=<id>&token=<jwt> 打开；
数据文件按扫描所属机构鉴权（与报告资源一致）。
"""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..deps import get_org_scope
from ..models import Scan

router = APIRouter()
_STATIC_DIR = Path(__file__).resolve().parents[1] / "static" / "preview"


def _work_dir(scan_id: int, settings: Settings) -> Path:
    return (Path(settings.data_dir) / "work" / str(scan_id)).resolve()


def _verified_point_preview(work: Path) -> Path:
    """只发布显式验收的预览；否则回退原始 SLAM3R 基线。

    点数增加和 registration_validation 标记都不足以证明视觉质量；
    未验收的融合/补全结果不得再自动覆盖正式页面。
    """
    postprocess = (work / "postprocess").resolve()
    selection = postprocess / "preview_selection.json"
    if selection.is_file():
        try:
            payload = json.loads(selection.read_text(encoding="utf-8"))
            filename = str(payload.get("accepted_file") or "").strip()
            candidate = (postprocess / filename).resolve()
            if (
                filename
                and candidate.is_relative_to(postprocess)
                and candidate.suffix.lower() == ".ply"
                and candidate.is_file()
            ):
                return candidate
        except (OSError, ValueError, TypeError):
            pass
    return postprocess / "scene_preview.ply"


def _preview_point_count(path: Path, alignment: dict) -> int | None:
    """优先返回正式展示副本的实际点数，旧文件才回退 alignment。"""
    diagnostic = path.with_suffix(".json")
    if diagnostic.is_file():
        try:
            value = int(json.loads(diagnostic.read_text(encoding="utf-8")).get("output_points") or 0)
            if value > 0:
                return value
        except (OSError, ValueError, TypeError):
            pass
    try:
        with path.open("rb") as stream:
            header = stream.read(8192).decode("ascii", errors="ignore")
        for line in header.splitlines():
            parts = line.strip().split()
            if len(parts) == 3 and parts[0] == "element" and parts[1] == "vertex":
                value = int(parts[2])
                if value > 0:
                    return value
    except (OSError, ValueError, TypeError):
        pass
    value = alignment.get("points_preview")
    return int(value) if value is not None else None


@router.get("/{scan_id}/manifest.json")
def preview_manifest(
    scan_id: int,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    scan = db.get(Scan, scan_id)
    if scan is None or scan.project.org_id != org_id:
        raise HTTPException(404, "扫描任务不存在")
    work = _work_dir(scan_id, settings)
    alignment_path = work / "postprocess" / "alignment.json"
    alignment = (
        json.loads(alignment_path.read_text(encoding="utf-8"))
        if alignment_path.is_file()
        else {}
    )
    # 优先整段视频观测融合的展示副本；质量门槛失败则回退原始预览。
    # 测量/风险始终继续读取 scene_aligned.ply。
    preview_ply = _verified_point_preview(work)
    layout_json = next(
        (p for p in (
            work / "postprocess" / "layout_boxes.json",
            work / "postprocess" / "layout.json",
        ) if p.is_file()), None
    )
    if not preview_ply.is_file():
        raise HTTPException(404, "预览尚未生成")
    gaussian_ply = work / "gaussian" / "gaussian.ply"
    gaussian_web_ply = work / "gaussian" / "gaussian_web.ply"
    gaussian_web_splat = work / "gaussian" / "gaussian_web.splat"
    structure_json = work / "postprocess" / "structure.json"
    calibrated_structure_json = work / "postprocess" / "structure_calibrated.json"
    measurements_json = work / "postprocess" / "measurements.json"
    passage_analysis_json = work / "postprocess" / "passage_analysis.json"
    spatial_foundation_json = work / "postprocess" / "spatial_foundation.json"
    passage_analysis_png = work / "postprocess" / "passage_analysis.png"
    return {
        "scan_id": scan_id,
        "name": scan.project.name if scan.project else f"扫描 #{scan_id}",
        # 同一扫描的展示点云可能在增量补训后被原子替换；使用文件版本号
        # 避免浏览器继续显示补训前缓存（尤其会把已修复颜色误看成全黑）。
        "ply": f"/api/preview/{scan_id}/scene.ply?v={preview_ply.stat().st_mtime_ns}",
        "gaussian_ply": (
            f"/api/preview/{scan_id}/gaussian-web.splat"
            if gaussian_web_splat.is_file()
            else f"/api/preview/{scan_id}/gaussian-web.ply"
            if gaussian_web_ply.is_file()
            else (f"/api/preview/{scan_id}/gaussian.ply" if gaussian_ply.is_file() else None)
        ),
        "layout": f"/api/preview/{scan_id}/layout.json" if layout_json.is_file() else None,
        "structure": f"/api/preview/{scan_id}/structure.json" if (calibrated_structure_json.is_file() or structure_json.is_file()) else None,
        "measurements": f"/api/preview/{scan_id}/measurements.json" if measurements_json.is_file() else None,
        "passage_analysis": f"/api/preview/{scan_id}/passage-analysis.json" if passage_analysis_json.is_file() else None,
        "spatial_foundation": f"/api/preview/{scan_id}/spatial-foundation.json" if spatial_foundation_json.is_file() else None,
        "passage_figure": f"/api/preview/{scan_id}/passage-analysis.png" if passage_analysis_png.is_file() else None,
        "alignment": alignment,
        "preview_points": _preview_point_count(preview_ply, alignment),
        "preview_source": preview_ply.name,
        "status": scan.status,
    }


@router.get("/{scan_id}/gaussian.ply", response_class=FileResponse)
def preview_gaussian(
    scan_id: int,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    scan = db.get(Scan, scan_id)
    if scan is None or scan.project.org_id != org_id:
        raise HTTPException(404, "扫描任务不存在")
    work = _work_dir(scan_id, settings)
    path = (work / "gaussian" / "gaussian.ply").resolve()
    if not path.is_relative_to(work) or not path.is_file():
        raise HTTPException(404, "Gaussian 场景不存在")
    return FileResponse(path, media_type="application/octet-stream")


@router.get("/{scan_id}/gaussian-web.ply", response_class=FileResponse)
def preview_gaussian_web(
    scan_id: int,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    scan = db.get(Scan, scan_id)
    if scan is None or scan.project.org_id != org_id:
        raise HTTPException(404, "扫描任务不存在")
    work = _work_dir(scan_id, settings)
    path = (work / "gaussian" / "gaussian_web.ply").resolve()
    if not path.is_relative_to(work) or not path.is_file():
        raise HTTPException(404, "Gaussian 网页场景不存在")
    return FileResponse(path, media_type="application/octet-stream")


@router.get("/{scan_id}/gaussian-web.splat", response_class=FileResponse)
def preview_gaussian_web_splat(
    scan_id: int,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    scan = db.get(Scan, scan_id)
    if scan is None or scan.project.org_id != org_id:
        raise HTTPException(404, "扫描任务不存在")
    work = _work_dir(scan_id, settings)
    path = (work / "gaussian" / "gaussian_web.splat").resolve()
    if not path.is_relative_to(work) or not path.is_file():
        raise HTTPException(404, "Gaussian 网页场景不存在")
    return FileResponse(path, media_type="application/octet-stream")


@router.get("/{scan_id}/scene.ply", response_class=FileResponse)
def preview_pointcloud(
    scan_id: int,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    scan = db.get(Scan, scan_id)
    if scan is None or scan.project.org_id != org_id:
        raise HTTPException(404, "扫描任务不存在")
    work = _work_dir(scan_id, settings)
    # 这里只发布训练后生成的原始彩色预览点云。任何融合候选必须先通过
    # 独立质量验证，不能因文件存在就自动进入正式页面。
    path = _verified_point_preview(work).resolve()
    if not path.is_relative_to(work) or not path.is_file():
        raise HTTPException(404, "预览点云不存在")
    return FileResponse(path, media_type="application/octet-stream")


@router.get("/{scan_id}/layout.json")
def preview_layout(
    scan_id: int,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    scan = db.get(Scan, scan_id)
    if scan is None or scan.project.org_id != org_id:
        raise HTTPException(404, "扫描任务不存在")
    work = _work_dir(scan_id, settings)
    path = next(
        (p for p in (
            work / "postprocess" / "layout_boxes.json",
            work / "postprocess" / "layout.json",
        ) if p.is_file()), None
    )
    if path is None or not path.is_relative_to(work):
        raise HTTPException(404, "结构识别结果不存在")
    return FileResponse(path, media_type="application/json")


@router.get("/{scan_id}/structure.json")
def preview_structure(
    scan_id: int,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    scan = db.get(Scan, scan_id)
    if scan is None or scan.project.org_id != org_id:
        raise HTTPException(404, "扫描任务不存在")
    work = _work_dir(scan_id, settings)
    path = next((candidate.resolve() for candidate in (
        work / "postprocess" / "structure_calibrated.json",
        work / "postprocess" / "structure.json",
    ) if candidate.is_file()), (work / "postprocess" / "structure.json").resolve())
    if not path.is_relative_to(work) or not path.is_file():
        raise HTTPException(404, "空间结构结果不存在")
    return FileResponse(path, media_type="application/json")


@router.get("/{scan_id}/structure_plan.png", response_class=FileResponse)
def preview_structure_plan(
    scan_id: int,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    scan = db.get(Scan, scan_id)
    if scan is None or scan.project.org_id != org_id:
        raise HTTPException(404, "扫描任务不存在")
    work = _work_dir(scan_id, settings)
    path = (work / "postprocess" / "structure_plan.png").resolve()
    if not path.is_relative_to(work) or not path.is_file():
        raise HTTPException(404, "结构图不存在")
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


@router.get("/{scan_id}/measurements.json")
def preview_measurements(
    scan_id: int,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    scan = db.get(Scan, scan_id)
    if scan is None or scan.project.org_id != org_id:
        raise HTTPException(404, "扫描任务不存在")
    work = _work_dir(scan_id, settings)
    path = (work / "postprocess" / "measurements.json").resolve()
    if not path.is_relative_to(work) or not path.is_file():
        raise HTTPException(404, "长度测量结果不存在")
    return FileResponse(path, media_type="application/json")


def _postprocess_file(scan_id: int, filename: str, db: Session, org_id: int, settings: Settings) -> Path:
    scan = db.get(Scan, scan_id)
    if scan is None or scan.project.org_id != org_id:
        raise HTTPException(404, "扫描任务不存在")
    work = _work_dir(scan_id, settings)
    path = (work / "postprocess" / filename).resolve()
    if not path.is_relative_to(work) or not path.is_file():
        raise HTTPException(404, "空间基础数据尚未生成")
    return path


@router.get("/{scan_id}/passage-analysis.json", response_class=FileResponse)
def preview_passage_analysis(
    scan_id: int, db: Session = Depends(get_db), org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    return FileResponse(
        _postprocess_file(scan_id, "passage_analysis.json", db, org_id, settings),
        media_type="application/json",
    )


@router.get("/{scan_id}/spatial-foundation.json", response_class=FileResponse)
def preview_spatial_foundation(
    scan_id: int, db: Session = Depends(get_db), org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    return FileResponse(
        _postprocess_file(scan_id, "spatial_foundation.json", db, org_id, settings),
        media_type="application/json",
    )


@router.get("/{scan_id}/passage-analysis.png", response_class=FileResponse)
def preview_passage_figure(
    scan_id: int, db: Session = Depends(get_db), org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    return FileResponse(
        _postprocess_file(scan_id, "passage_analysis.png", db, org_id, settings),
        media_type="image/png",
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )

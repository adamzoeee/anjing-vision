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
    preview_ply = work / "postprocess" / "scene_preview.ply"
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
    return {
        "scan_id": scan_id,
        "name": scan.project.name if scan.project else f"扫描 #{scan_id}",
        "ply": f"/api/preview/{scan_id}/scene.ply",
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
        "alignment": alignment,
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
    path = (work / "postprocess" / "scene_preview.ply").resolve()
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
    return FileResponse(path, media_type="image/png")


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

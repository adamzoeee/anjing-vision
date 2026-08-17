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
    layout_json = work / "postprocess" / "layout_boxes.json"
    if not preview_ply.is_file():
        raise HTTPException(404, "预览尚未生成")
    return {
        "scan_id": scan_id,
        "name": scan.project.name if scan.project else f"扫描 #{scan_id}",
        "ply": f"/api/preview/{scan_id}/scene.ply",
        "layout": f"/api/preview/{scan_id}/layout.json" if layout_json.is_file() else None,
        "alignment": alignment,
        "status": scan.status,
    }


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
    path = (work / "postprocess" / "layout_boxes.json").resolve()
    if not path.is_relative_to(work) or not path.is_file():
        raise HTTPException(404, "结构识别结果不存在")
    return FileResponse(path, media_type="application/json")

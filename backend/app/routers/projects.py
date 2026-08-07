from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..deps import get_org_scope
from ..models import Project, Scan
from ..schemas import ProjectIn, ProjectOut, ScanIn, ScanOut
router = APIRouter()


def _page_bounds(
    offset: int = Query(default=0, ge=0),
    limit: int | None = Query(default=None, ge=1),
    settings: Settings = Depends(get_settings),
) -> tuple[int, int]:
    page_size = limit or settings.default_page_size
    if page_size > settings.max_page_size:
        raise HTTPException(422, f"limit 不能超过 {settings.max_page_size}")
    return offset, page_size


@router.get("", response_model=list[ProjectOut])
def list_projects(
    page: tuple[int, int] = Depends(_page_bounds),
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
):
    offset, limit = page
    return (
        db.query(Project)
        .filter(Project.org_id == org_id)
        .order_by(Project.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.post("", response_model=ProjectOut)
def create_project(data: ProjectIn, db: Session = Depends(get_db), org_id: int = Depends(get_org_scope)):
    p = Project(org_id=org_id, name=data.name, address=data.address)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db), org_id: int = Depends(get_org_scope)):
    p = db.get(Project, project_id)
    if p is None or p.org_id != org_id:
        raise HTTPException(404, "项目不存在")
    return p


@router.post("/{project_id}/scans", response_model=ScanOut)
def create_scan(project_id: int, data: ScanIn, db: Session = Depends(get_db),
                org_id: int = Depends(get_org_scope)):
    p = db.get(Project, project_id)
    if p is None or p.org_id != org_id:
        raise HTTPException(404, "项目不存在")
    scan = Scan(project_id=project_id, capture_type=data.capture_type)
    db.add(scan)
    db.commit()
    db.refresh(scan)
    return scan


@router.get("/{project_id}/scans", response_model=list[ScanOut])
def list_scans(
    project_id: int,
    page: tuple[int, int] = Depends(_page_bounds),
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
):
    p = db.get(Project, project_id)
    if p is None or p.org_id != org_id:
        raise HTTPException(404, "项目不存在")
    offset, limit = page
    return (
        db.query(Scan)
        .filter(Scan.project_id == project_id)
        .order_by(Scan.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

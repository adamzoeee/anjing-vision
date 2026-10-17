"""Authenticated shared capture; configuration is server-side, never arbitrary client paths."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..deps import get_current_user
from ..models import User

router = APIRouter()


def _manager(request: Request):
    return request.app.state.live_capture


def _owned(manager, user):
    try: manager.assert_owner(user.org_id)
    except PermissionError as exc: raise HTTPException(403, str(exc)) from exc


def _status(request, user):
    try:
        return {"enabled":request.app.state.settings.realtime_enabled,
                **_manager(request).status(owner_org_id=user.org_id)}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/status")
def capture_status(request: Request, response: Response, user: User = Depends(get_current_user)):
    _owned(_manager(request), user)
    response.headers["Cache-Control"] = "no-store"
    return _status(request, user)


@router.post("/start")
def start_capture(request: Request, user: User = Depends(get_current_user)):
    if user.role != "admin": raise HTTPException(403, "仅管理员可以启动或停止摄像头")
    if not request.app.state.settings.realtime_enabled: raise HTTPException(503, "实时采集未启用，请由管理员配置 REALTIME_ENABLED")
    try: _manager(request).start(user.org_id)
    except PermissionError as exc: raise HTTPException(403, str(exc)) from exc
    except RuntimeError as exc: raise HTTPException(409, str(exc)) from exc
    return _status(request, user)


@router.post("/stop")
def stop_capture(request: Request, user: User = Depends(get_current_user)):
    if user.role != "admin": raise HTTPException(403, "仅管理员可以启动或停止摄像头")
    manager = _manager(request)
    _owned(manager, user)
    if not manager.stop(user.org_id): raise HTTPException(409, "采集停止超时，请检查状态，禁止重复打开设备")
    return _status(request, user)


@router.get("/frame.jpg")
def capture_frame(request: Request, user: User = Depends(get_current_user)):
    manager = _manager(request)
    _owned(manager, user)
    if not request.app.state.settings.realtime_enabled: raise HTTPException(503, "实时采集未启用")
    if not manager.config.preview_enabled: raise HTTPException(403, "预览已关闭，其他消费者仍可继续采集")
    try: packet = manager.preview_jpeg(owner_org_id=user.org_id)
    except PermissionError as exc: raise HTTPException(403, str(exc)) from exc
    headers = {"Cache-Control":"no-store", "Vary":"Authorization"}
    if packet is None: return Response(status_code=204, headers=headers)
    frame, jpeg = packet
    return Response(jpeg, media_type="image/jpeg", headers={**headers,
        "X-Frame-Id":str(frame.frame_id),"X-Capture-Session":frame.session_id,
        "X-Frame-Timestamp":str(frame.timestamp)})

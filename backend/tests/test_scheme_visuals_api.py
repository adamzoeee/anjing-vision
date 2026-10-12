"""方案可视化：改造后的 2D 平面图与通行图（只读、与报告同源渲染）。

用户口径：每个保存的方案都要能“看到”干完后的样子（家居框架图 / 平面图 / 通行图），
不只是算出一个分数。平面图与通行图由服务端按方案派生状态重渲染，输入只读、
不落盘、不改真实数据；改造前的两张图直接复用扫描自己的报告资源。
"""
import io
import json
from pathlib import Path

import PIL.Image

from app.config import get_settings


def _auth(client, org="养老院A", email="visuals@x.com"):
    client.post("/api/auth/register", json={
        "org_name": org, "name": "甲", "email": email, "password": "secret123"})
    r = client.post("/api/auth/login", json={"email": email, "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _scan_with_artifacts(client, headers, measurements, structure):
    pid = client.post("/api/projects", json={"name": "可视化房间"}, headers=headers).json()["id"]
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"},
                      headers=headers).json()["id"]
    post = Path(get_settings().data_dir) / "work" / str(sid) / "postprocess"
    post.mkdir(parents=True, exist_ok=True)
    (post / "measurements.json").write_text(json.dumps(measurements, ensure_ascii=False), encoding="utf-8")
    (post / "structure_calibrated.json").write_text(json.dumps(structure, ensure_ascii=False), encoding="utf-8")
    return sid


def _create_scheme(client, headers, sid, name, edits):
    created = client.post(f"/api/assistant/scans/{sid}/schemes",
                          json={"name": name, "edits": edits}, headers=headers)
    assert created.status_code == 200, created.text
    return created.json()["id"]


def _png(client, headers, sid, scheme_id, kind):
    res = client.get(f"/api/assistant/scans/{sid}/schemes/{scheme_id}/{kind}", headers=headers)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "image/png"
    assert res.content.startswith(b"\x89PNG"), f"{kind} must be a real PNG"
    image = PIL.Image.open(io.BytesIO(res.content))
    assert image.size[0] > 300 and image.size[1] > 300
    return res.content


def test_scheme_visuals_render_before_and_after_distinctly(client, scheme_inputs):
    """同一扫描：动过书桌的方案与未动方案的平面图/通行图必须不同（证明是派生状态在渲染）。"""
    m, s = scheme_inputs
    headers = _auth(client)
    sid = _scan_with_artifacts(client, headers, m, s)
    moved = _create_scheme(client, headers, sid, "书桌下移",
                           [{"op": "move", "target": "desk_001", "dx": 0.0, "dy": -0.6}])
    untouched = _create_scheme(client, headers, sid, "原样基线", [])

    plan_moved = _png(client, headers, sid, moved, "plan.png")
    plan_base = _png(client, headers, sid, untouched, "plan.png")
    passage_moved = _png(client, headers, sid, moved, "passage.png")
    passage_base = _png(client, headers, sid, untouched, "passage.png")

    assert plan_moved != plan_base, "修改过的方案平面图必须反映派生布局"
    assert passage_moved != passage_base, "修改过的方案通行图必须反映派生布局"


def test_scheme_visuals_are_org_scoped_and_404_for_unknown(client, scheme_inputs):
    m, s = scheme_inputs
    headers = _auth(client)
    sid = _scan_with_artifacts(client, headers, m, s)
    scheme_id = _create_scheme(client, headers, sid, "方案", [])

    other = _auth(client, org="养老院B", email="visuals_b@x.com")
    assert client.get(f"/api/assistant/scans/{sid}/schemes/{scheme_id}/plan.png",
                      headers=other).status_code == 404
    assert client.get(f"/api/assistant/scans/{sid}/schemes/{scheme_id}/passage.png",
                      headers=other).status_code == 404
    assert client.get(f"/api/assistant/scans/{sid}/schemes/999999/plan.png",
                      headers=headers).status_code == 404
    assert client.get(f"/api/assistant/scans/{sid}/schemes/999999/passage.png",
                      headers=headers).status_code == 404


def test_scheme_visuals_never_touch_real_artifacts(client, scheme_inputs):
    """渲染走内存派生状态：响应后 postprocess 目录里的真实产物保持原样与数量。"""
    m, s = scheme_inputs
    headers = _auth(client)
    sid = _scan_with_artifacts(client, headers, m, s)
    scheme_id = _create_scheme(client, headers, sid, "只读校验",
                               [{"op": "move", "target": "desk_001", "dx": 0.0, "dy": -0.6}])
    post = Path(get_settings().data_dir) / "work" / str(sid) / "postprocess"
    before = {path.name: path.read_bytes() for path in sorted(post.iterdir())}

    _png(client, headers, sid, scheme_id, "plan.png")
    _png(client, headers, sid, scheme_id, "passage.png")

    after = {path.name: path.read_bytes() for path in sorted(post.iterdir())}
    assert after == before, "方案可视化必须只读：不得新增/改写任何真实产物"

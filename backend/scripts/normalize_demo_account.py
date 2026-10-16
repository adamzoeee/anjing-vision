# 演示账户归位脚本（幂等、默认预演）：
# 1) 把演示账号收敛成独立机构下的真实账号（邮箱 + 密码登录，不再是演示旁路）；
# 2) 把原演示账号名下的项目（如「我的全黑家」「家庭点云整屋演示」）划到该账号；
# 3) 删除既无用户也无项目的空壳机构；
# 4) 重编号机构 / 用户 / 项目，让演示账户成为系统第一个账户。
# 扫描（scan）id 不变，因此 data/work/<scan_id>、media/<scan_id> 等磁盘目录无需重命名。
# 重编号一律走「插入新行 → 重定向引用 → 删除旧行」，避免外键即时校验失败。
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import engine as default_engine  # noqa: E402
from app.security import hash_password  # noqa: E402


def _fetch(conn, sql: str, **params):
    return conn.execute(text(sql), params).fetchall()


def _one(conn, sql: str, **params):
    rows = _fetch(conn, sql, **params)
    return rows[0] if rows else None


def _free_ids(conn, table: str, count: int) -> list[int]:
    taken = {row[0] for row in _fetch(conn, f"SELECT id FROM {table}")}
    out: list[int] = []
    candidate = 1
    while len(out) < count:
        if candidate not in taken:
            out.append(candidate)
        candidate += 1
    return out


def _describe(conn) -> None:
    print("  机构:", _fetch(conn, "SELECT id, name FROM organizations ORDER BY id"))
    print("  用户:", _fetch(conn, "SELECT id, org_id, email, role FROM users ORDER BY id"))
    print("  项目:", _fetch(conn, "SELECT id, org_id, name FROM projects ORDER BY id"))
    print("  扫描:", _fetch(conn, "SELECT id, project_id, capture_type FROM scans ORDER BY id"))


def _ensure_org(conn, *, org_name: str, legacy_org_id: int | None) -> int:
    org = _one(conn, "SELECT id FROM organizations WHERE name = :name", name=org_name)
    if org is not None:
        return org[0]
    if legacy_org_id is not None:
        conn.execute(
            text("UPDATE organizations SET name = :name WHERE id = :id"),
            {"name": org_name, "id": legacy_org_id},
        )
        return legacy_org_id
    new_id = _free_ids(conn, "organizations", 1)[0]
    conn.execute(
        text("INSERT INTO organizations (id, name) VALUES (:id, :name)"),
        {"id": new_id, "name": org_name},
    )
    return new_id


def _ensure_user(
    conn,
    *,
    email: str,
    password: str,
    display_name: str,
    legacy_email: str,
    org_id: int,
) -> int:
    """确保演示账号存在并可用；返回账号 id。"""
    existing = _one(conn, "SELECT id, org_id FROM users WHERE email = :email", email=email)
    if existing is not None:
        user_id = existing[0]
        conn.execute(
            text(
                "UPDATE users SET name = :name, org_id = :org, role = 'admin', "
                "disabled_at = NULL WHERE id = :id"
            ),
            {"name": display_name, "org": org_id, "id": user_id},
        )
        legacy = _one(conn, "SELECT id, org_id FROM users WHERE email = :email", email=legacy_email)
        if legacy is not None:
            print(
                f"  ! 旧演示账号仍存在（id={legacy[0]} {legacy_email}）："
                "已把项目划到演示机构，旧账号保留待人工确认"
            )
        return user_id

    legacy = _one(conn, "SELECT id, org_id FROM users WHERE email = :email", email=legacy_email)
    if legacy is not None:
        # 沿用原账号行：保留 created_at 与改造方案的 created_by 归属
        conn.execute(
            text(
                "UPDATE users SET email = :email, name = :name, org_id = :org, role = 'admin', "
                "password_hash = :pw, disabled_at = NULL, updated_at = NULL WHERE id = :id"
            ),
            {
                "email": email,
                "name": display_name,
                "org": org_id,
                "pw": hash_password(password),
                "id": legacy[0],
            },
        )
        return legacy[0]

    new_id = _free_ids(conn, "users", 1)[0]
    conn.execute(
        text(
            "INSERT INTO users (id, org_id, name, email, password_hash, role) "
            "VALUES (:id, :org, :name, :email, :pw, 'admin')"
        ),
        {
            "id": new_id,
            "org": org_id,
            "name": display_name,
            "email": email,
            "pw": hash_password(password),
        },
    )
    return new_id


def _drop_empty_orgs(conn, keep_org_id: int) -> None:
    conn.execute(
        text(
            "DELETE FROM organizations WHERE id <> :keep "
            "AND id NOT IN (SELECT org_id FROM users) "
            "AND id NOT IN (SELECT org_id FROM projects)"
        ),
        {"keep": keep_org_id},
    )


def _renumber_org(conn, org_id: int) -> int:
    target = _free_ids(conn, "organizations", 1)[0]
    if target == org_id:
        return org_id
    # 机构名唯一：先把旧行改名，插入新行后再删旧行
    name = _one(conn, "SELECT name FROM organizations WHERE id = :id", id=org_id)[0]
    conn.execute(
        text("UPDATE organizations SET name = :parked WHERE id = :id"),
        {"parked": f"{name}.renumbering", "id": org_id},
    )
    conn.execute(
        text("INSERT INTO organizations (id, name, created_at) VALUES (:new, :name, :created)"),
        {
            "new": target,
            "name": name,
            "created": _one(
                conn, "SELECT created_at FROM organizations WHERE id = :id", id=org_id
            )[0],
        },
    )
    conn.execute(
        text("UPDATE users SET org_id = :new WHERE org_id = :old"),
        {"new": target, "old": org_id},
    )
    conn.execute(
        text("UPDATE projects SET org_id = :new WHERE org_id = :old"),
        {"new": target, "old": org_id},
    )
    conn.execute(text("DELETE FROM organizations WHERE id = :old"), {"old": org_id})
    return target


def _renumber_user(conn, user_id: int, org_id: int) -> int:
    target = _free_ids(conn, "users", 1)[0]
    if target == user_id:
        return user_id
    parked = _one(conn, "SELECT email FROM users WHERE id = :id", id=user_id)[0]
    # 邮箱唯一 + 每机构唯一管理员：旧行先改名并降级为 member，避免插入期间冲突
    conn.execute(
        text("UPDATE users SET email = :parked, role = 'member' WHERE id = :id"),
        {"parked": f"{parked}.renumbering", "id": user_id},
    )
    conn.execute(
        text(
            "INSERT INTO users (id, org_id, name, email, password_hash, role, disabled_at, "
            "created_at, updated_at) SELECT :new, :org, name, :final, password_hash, 'admin', "
            "disabled_at, created_at, updated_at FROM users WHERE id = :old"
        ),
        {"new": target, "org": org_id, "final": parked, "old": user_id},
    )
    conn.execute(
        text("UPDATE renovation_schemes SET created_by = :new WHERE created_by = :old"),
        {"new": target, "old": user_id},
    )
    conn.execute(text("DELETE FROM users WHERE id = :old"), {"old": user_id})
    return target


def _renumber_projects(conn, org_id: int) -> None:
    rows = _fetch(
        conn,
        "SELECT id FROM projects WHERE org_id = :org ORDER BY created_at, id",
        org=org_id,
    )
    old_ids = [row[0] for row in rows]
    for old_id, new_id in zip(old_ids, _free_ids(conn, "projects", len(old_ids))):
        if old_id == new_id:
            continue
        conn.execute(
            text(
                "INSERT INTO projects (id, org_id, name, address, created_at) "
                "SELECT :new, org_id, name, address, created_at FROM projects WHERE id = :old"
            ),
            {"new": new_id, "old": old_id},
        )
        conn.execute(
            text("UPDATE scans SET project_id = :new WHERE project_id = :old"),
            {"new": new_id, "old": old_id},
        )
        conn.execute(text("DELETE FROM projects WHERE id = :old"), {"old": old_id})


def normalize(
    conn,
    *,
    org_name: str,
    email: str,
    password: str,
    legacy_email: str,
    display_name: str,
    renumber: bool,
) -> None:
    legacy_user = _one(
        conn, "SELECT id, org_id FROM users WHERE email = :email", email=legacy_email
    )
    org_id = _ensure_org(
        conn,
        org_name=org_name,
        legacy_org_id=legacy_user[1] if legacy_user is not None else None,
    )
    user_id = _ensure_user(
        conn,
        email=email,
        password=password,
        display_name=display_name,
        legacy_email=legacy_email,
        org_id=org_id,
    )
    # 演示项目归位：原演示机构名下的项目全部划到演示机构
    if legacy_user is not None and legacy_user[1] != org_id:
        conn.execute(
            text("UPDATE projects SET org_id = :new WHERE org_id = :old"),
            {"new": org_id, "old": legacy_user[1]},
        )
    _drop_empty_orgs(conn, org_id)

    if renumber:
        org_id = _renumber_org(conn, org_id)
        user_id = _renumber_user(conn, user_id, org_id)
        _renumber_projects(conn, org_id)

    print(f"  演示机构 id={org_id}「{org_name}」")
    print(f"  演示账号 id={user_id} {email}（admin · 密码 = 账户名）")


def main() -> int:
    parser = argparse.ArgumentParser(description="演示账户归位与空壳机构清理")
    parser.add_argument("--org-name", default="演示机构")
    parser.add_argument("--email", default="demo@anjing-vision.com")
    parser.add_argument("--password", default=None, help="默认与 --email 相同")
    parser.add_argument("--display-name", default="演示账号")
    parser.add_argument("--legacy-email", default="demo@demo.com")
    parser.add_argument("--no-renumber", action="store_true", help="不重编号，只归位与清理")
    parser.add_argument("--apply", action="store_true", help="写入；缺省为预演")
    args = parser.parse_args()
    password = args.password or args.email

    print(f"数据库: {default_engine.url.render_as_string(hide_password=True)}")
    with default_engine.connect() as conn:
        print("变更前:")
        _describe(conn)
        normalize(
            conn,
            org_name=args.org_name,
            email=args.email,
            password=password,
            legacy_email=args.legacy_email,
            display_name=args.display_name,
            renumber=not args.no_renumber,
        )
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
        print("变更后:")
        _describe(conn)
    if not args.apply:
        print("预演结束：未写入。加 --apply 生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

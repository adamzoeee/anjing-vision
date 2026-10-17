# 账号与权限

[返回项目首页](../README.md) · [项目索引](INDEX.md)

本页描述工作台账号的注册、登录、权限与演示账户。工作台（`/console`）是业务界面。

## 层级与归属

```text
Organization 机构 ←── 数据隔离边界（project.org_id 决定可见范围）
└── User 用户       ←── 角色边界（admin 唯一 / member 只读）
    └── Project 项目
        └── Scan 扫描（video / photos / house / house_demo）
            ├── Report 报告
            └── RenovationScheme 改造方案
```

**机构。** 用户属于且仅属于一个机构，不跨机构。所有资源查询都强制带 `org_id` 过滤，跨机构访问一律返回 404，不透露"存在但无权"。JWT 同时携带 `sub`（用户 id）与 `org`（机构 id），每次请求校验 `token.org == user.org_id`，不一致返回 403。

**用户。** 角色只有 `admin` 与 `member` 两种，由 `users.role` 保存。每机构最多一个管理员，由数据库唯一索引 `uq_users_single_admin_per_org` 保证。账号不做物理删除：停用即写 `users.disabled_at` 时间戳，保留历史归属（如改造方案的 `created_by`）。

**注册。** 登录页可创建新机构及其唯一管理员。已有机构不能通过公开注册加入；成员由该机构管理员在「用户管理」中创建。

**会话。** 登录只走邮箱 + 密码（bcrypt 校验），签发 JWT，没有服务端会话也没有 refresh token，默认有效期 7 天（`TOKEN_EXPIRE_MINUTES`）。停用立即生效——每次请求都会重新读取账号状态。本人修改密码或管理员重置成员密码后，旧 token 立即失效，需用新密码重新登录。

## 权限矩阵

服务端是唯一判定点：`get_current_user` 负责认证与机构一致性，`require_admin` 负责角色，控制台只做入口隐藏。

| 操作 | admin | member |
|---|---|---|
| 项目 / 扫描 / 报告 / PDF / 点云 / 结构图 / 通行图 读取 | ✅ | ✅ |
| 改造建议、沙盒模拟 `/simulate`、`/solve`（只读计算） | ✅ | ✅ |
| 新建项目、新建房间扫描、上传视频、写参考尺寸 | ✅ | ❌ 403 |
| 整屋拼接、导入合成家庭演示包 | ✅ | ❌ 403 |
| 保存 / 改名 / 删除改造方案、导出方案 PDF | ✅ | ❌ 403 |
| 机构成员列表、建号、改名、重置密码、停用启用 | ✅ | ❌ 403 |
| 修改自己的密码 | ✅ | ✅（需原密码） |

接口清单：

| 路由 | 说明 |
|---|---|
| `POST /api/auth/register` | 公开创建新机构及其管理员；机构名称已存在返回 409 |
| `POST /api/auth/login` | 登录，唯一入口 |
| `POST /api/auth/password` | 本人改密，需 `current_password` |
| `GET /api/auth/me` | 当前账号画像（含 `role`、`is_active`、`org_name`） |
| `GET /api/users` | 本机构成员列表 |
| `POST /api/users` | 建号，角色固定为 member，可用 `disabled_at` 停用 |
| `PATCH /api/users/{id}` | 改名 / 启用 / 停用 |
| `POST /api/users/{id}/password` | 重置成员密码（不需要原密码） |

管理员账号不能通过 `/api/users` 被停用、改名或重置密码（返回 409），也不能对自己操作（返回 400）——每机构一个管理员。所有登录用户都可以在侧边栏使用「修改密码」，管理员也可从用户管理页进入。

## 演示账户

演示数据收敛到一个独立机构，不再依赖登录旁路：

| 项 | 值 |
|---|---|
| 机构 | 演示机构（机构 id 1，系统第一个账户） |
| 账号 | `demo@anjing-vision.com` |
| 密码 | 与账号相同，即 `demo@anjing-vision.com` |
| 角色 | admin |
| 项目 | 我的全黑家（项目 1）、家庭点云整屋演示（项目 2） |

扫描 id 未变（46 / 47），因此 `backend/data/work/<scan_id>`、`backend/data/media/<scan_id>` 等磁盘目录保持原样，不需要重命名。

首次部署或把既有本地库归位：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\normalize_demo_account.py            # 预演，不写库
.\.venv\Scripts\python.exe scripts\normalize_demo_account.py --apply    # 生效
```

脚本是幂等的：确保演示机构与账号存在、账号角色为 admin 且密码为账号名、把原演示机构名下的项目划到该机构、删除既无用户也无项目的空壳机构，最后把机构 / 用户 / 项目重编号到最小 id，使演示账户成为第一个账户。它不删除任何扫描、报告或磁盘数据；重编号一律走"插入新行 → 重定向引用 → 删除旧行"，不依赖延迟外键。`--no-renumber` 可只做归位与清理。

## 数据库迁移

`users.disabled_at` 与 `users.updated_at` 由 Alembic 迁移 `20261016_0006` 添加：

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
```

`AUTO_CREATE_TABLES=true` 只建缺失的表，不会给既有表补列。既有库（`alembic_version` 为空、表结构等于旧 head）需要先 `alembic stamp 20260926_0005` 再 `upgrade head`。

## 已知边界

- 系统未接入邮件验证与找回密码服务；忘记密码的成员由机构管理员重置。管理员忘记密码需通过受信任的运维流程处理。
- 没有"机构之间迁移项目"的接口；跨机构协作需要新建机构并重建项目。

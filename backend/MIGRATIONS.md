# 数据库迁移

生产环境关闭 `AUTO_CREATE_TABLES`，数据库结构只通过 Alembic 迁移管理。

新数据库：

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

已有数据库在首次接入迁移前必须先备份。

如果数据库结构已经与 `20260807_0001_initial_schema.py` 一致，执行：

```powershell
.\.venv\Scripts\python.exe -m alembic stamp 20260807_0001
```

如果数据库由第三步之前的 SQLAlchemy `create_all` 创建，允许保留数据接入。
先确认同一扫描没有多份报告、同一机构没有多个管理员：

```sql
SELECT scan_id, COUNT(*) FROM reports GROUP BY scan_id HAVING COUNT(*) > 1;
SELECT org_id, COUNT(*) FROM users WHERE role = 'admin'
GROUP BY org_id HAVING COUNT(*) > 1;
```

查询均无结果后，执行：

```powershell
.\.venv\Scripts\python.exe -m alembic stamp 20260807_0001
.\.venv\Scripts\python.exe -m alembic upgrade head
```

`20260808_0003_reconcile_legacy_schema.py` 会保留原有业务数据，并补齐
`users.org_id`、`projects.org_id`、`scans.project_id` 索引以及
`reports.scan_id` 一对一约束。如果发现重复报告，迁移会明确中止且不会静默删除数据；
应先根据业务需要保留正确报告，再重新执行升级。

之后每次发布都先执行 `upgrade head`。开发和测试仍可通过
`AUTO_CREATE_TABLES=true` 使用 SQLite 自动建表。

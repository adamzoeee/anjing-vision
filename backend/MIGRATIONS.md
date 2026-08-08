# 数据库迁移

生产环境关闭 `AUTO_CREATE_TABLES`，数据库结构只通过 Alembic 迁移管理。

新数据库：

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

已有数据库在首次接入迁移前，应先备份并确认其结构与
`20260807_0001_initial_schema.py` 一致，然后执行：

```powershell
.\.venv\Scripts\python.exe -m alembic stamp 20260807_0001
```

之后每次发布都先执行 `upgrade head`。开发和测试仍可通过
`AUTO_CREATE_TABLES=true` 使用 SQLite 自动建表。

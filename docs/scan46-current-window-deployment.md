# 扫描46：当前窗户版本部署

使用 GitHub `main` 最新代码，并准备以下三个数据包，旧的两个包无需修改：

1. `scan46_完整数据包.zip`（保留原包，内含扫描45/46基础数据）
2. `scan46_最新结果补充包.zip`（之前的6.89MiB补充包）
3. `scan46_窗户增量补充包.zip`（仅本次窗户组件、实拍展示层和选择配置）

安装仓库要求的后端和 Flutter 依赖后，在仓库根目录执行，路径替换为实际文件位置：

```powershell
powershell -ExecutionPolicy Bypass -File backend\scripts\deploy_scan46.ps1 -FullBundlePath "D:\数据包\scan46_完整数据包.zip" -SupplementBundlePath "D:\数据包\scan46_最新结果补充包.zip" -WindowBundlePath "D:\数据包\scan46_窗户增量补充包.zip"
```

在停止后端时执行数据恢复。脚本严格按完整包、原补充包、窗户增量包的顺序恢复；已有数据库会备份。
三个包均由脚本自动解压，不需要手工合并。不要在第三个包之后重新覆盖旧包。
本地演示的后端配置应使用 `DATABASE_URL=sqlite:///./anjing-local.db`、`DATA_DIR=./data`、
`DEMO_LOGIN=true`、`DEMO_LOGIN_EMAIL=demo@demo.com`。
公开生产部署应按仓库生产部署说明配置真实账号和鉴权，禁止开启演示登录。

然后重启后端，并从仓库 `app` 目录启动或重新构建前端，API地址指向该后端。
本机演示可执行 `flutter run -d web-server --web-port 3000 --dart-define=DEMO_LOGIN=true --dart-define=API_BASE_URL=http://127.0.0.1:8000`。
旧浏览器缓存需要强制刷新。

## 核对

- 扫描46正式选择：`data/work/46/postprocess/window_components_reveal_review.ply`。
- 点数：1,023,460。
- 同名 `.visual.json` 为实拍窗户展示层，标记 `visual-only`，不参与尺寸、结构或风险计算。
- `/api/preview/46/manifest.json` 应含 `window_visual`，页面副标题应出现“窗户实拍展示层”。
- 正式评估分数保持80分；结构图、尺寸和评估使用原数据。
- 第二个包提供风险评估、通行图和PDF；第三个包只更新窗户点云及展示层。

无需重新运行 SLAM3R 或补训即可查看本版本。补充包用于复现已确认的展示结果，不包含全部历史实验和中间重建数组。

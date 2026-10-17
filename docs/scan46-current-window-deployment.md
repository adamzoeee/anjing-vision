# 扫描46：当前窗户版本部署

使用 GitHub `main` 最新代码，并准备以下三个数据包，旧的两个包无需修改：

1. `scan46_完整数据包.zip`（保留原包，内含扫描45/46基础数据）
2. `scan46_最新结果补充包.zip`（之前的6.89MiB补充包）
3. `scan46_窗户增量补充包.zip`（仅本次窗户组件、实拍展示层和选择配置）

安装仓库要求的后端依赖后，在仓库根目录执行，路径替换为实际文件位置：

```powershell
powershell -ExecutionPolicy Bypass -File backend\scripts\deploy_scan46.ps1 -FullBundlePath "D:\数据包\scan46_完整数据包.zip" -SupplementBundlePath "D:\数据包\scan46_最新结果补充包.zip" -WindowBundlePath "D:\数据包\scan46_窗户增量补充包.zip"
```

在停止后端时执行数据恢复。脚本严格按完整包、原补充包、窗户增量包的顺序恢复；已有数据库会备份。
三个包均由脚本自动解压，不需要手工合并。不要在第三个包之后重新覆盖旧包。
本地演示的后端配置应使用 `DATABASE_URL=sqlite:///./anjing-local.db`、`DATA_DIR=./data`。
登录用仓库演示账户（机构「演示机构」，账号 `demo@anjing-vision.com`，密码与账号相同），或接手方自己的账号。
演示登录旁路已于 2026-10-16 删除，公开部署请按生产说明配置真实账号和密钥，详见[账号与权限](accounts-and-permissions.md)。

然后重启后端，浏览器打开 `http://127.0.0.1:8000/` 登录工作台。业务界面只有工作台，没有独立前端服务；
工作台入口页与静态资产每次回源校验，旧浏览器缓存不需要手工清理。

## 核对

- 扫描46正式选择：`data/work/46/postprocess/window_components_reveal_review.ply`。
- 点数：1,023,460。
- 同名 `.visual.json` 为实拍窗户展示层，标记 `visual-only`，不参与尺寸、结构或风险计算。
- `/api/preview/46/manifest.json` 应含 `window_visual`，页面副标题应出现“窗户实拍展示层”。
- 正式评估分数保持80分；结构图、尺寸和评估使用原数据。
- 第二个包提供风险评估、通行图和PDF；第三个包只更新窗户点云及展示层。

无需重新运行 SLAM3R 或补训即可查看本版本。补充包用于复现已确认的展示结果，不包含全部历史实验和中间重建数组。

如需在另一台机器继续修改窗边点云，再追加桌面生成的
`scan46_训练与补拍研究增量包_20260921.zip`，并向部署脚本传入
`-ResearchBundlePath`。第四包包含前三包没有的补拍视频、局部重建数组、诊断结果和源机当前研究选择；详见
[`scan46-window-black-handoff.md`](scan46-window-black-handoff.md)。

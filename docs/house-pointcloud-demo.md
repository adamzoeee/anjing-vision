# 合成家庭点云与家具解析 Demo

## 定位与现有接口

独立展示模块，不代表真实视频重建质量，不参与真实扫描、尺寸标定或风险评分。
现有后端 `pipeline/spatiallm_runner.py::run_inference(point_cloud, output_txt, detect_type="all")`
调用已有独立 SpatialLM 环境，返回 `layout_txt / boxes_json / boxes / seconds`。
Demo 复用该接口及项目内置 Three.js、OrbitControls，不改模型、不新增检测算法、API 或数据库。

新增文件：

- `backend/pipeline/house_demo.py`：参数化表面采样、门户、家具几何、文件导出及串行模型适配。
- `backend/scripts/house_demo.py`：generate / infer / serve 入口。
- `backend/app/static/house_demo/index.html`、`demo.js`：独立点云界面。
- `backend/tests/test_house_demo.py`：几何、连接、导出和适配器测试。
- 本文档。

## 坐标与连接

全部文件使用米制世界坐标，Z 向上；无二次尺度拟合。房间零厚度墙面是表面点云，
不是施工 BIM。框架带厚度，门框在净空外，不生成门板遮挡。

| 房间 | 左下角世界坐标 | 尺寸 L/W/H (m) |
|---|---|---|
| 客厅 | 0, 0, 0 | 6 × 4 × 3 |
| 卧室 | 2, 4, 0 | 4 × 3.5 × 3 |
| 厨房 | 7.5, 4, 0 | 3 × 3 × 3 |
| 卫生间 | 7.5, 0, 0 | 2.5 × 2 × 3 |
| 走廊 | 6, 0, 0 | 1.5 × 7.5 × 3 |

四个房间分别通过同一个 PORTALS 定义在两侧墙面裁出 0.9 × 2.1 m 门洞，走廊连接全部房间。
共享墙采样在整屋导出时按坐标去重。每间独立文件仍保留完整结构。
默认表面网格约 5 cm，支持 2–15 cm；家具边界由多部件表面生成，不是随机内部填充。
书房可选项本版未添加。

## 本机运行

在项目 backend 目录执行；示例 Python 路径沿用本机已有后端环境。
所有输出位于 E 盘的 `backend/data/demos/`，不会写入 `data/work/<scan>`。
generate 拒绝覆盖非空目录；重新生成需指定一个新的目录。

```powershell
$python = 'E:\anlingzhijing\anjing-vision\.venv\python.exe'
$out = 'E:\anlingzhijing\anjing-vision-remote-latest\backend\data\demos\house_demo_20261012'
& $python scripts/house_demo.py generate --output $out

# 使用已有环境和已下载权重。也可以由当前项目 backend/.env 配置。
$env:SPATIALLM_DIR = 'E:\.PJs\spatiallm'
$env:SPATIALLM_PYTHON = 'E:\xinkeyan\miniconda\envs\spatiallm\python.exe'
$env:SPATIALLM_MODEL_PATH = 'E:\.PJs\models\SpatialLM1.1-Qwen-0.5B'
& $python scripts/house_demo.py infer --output $out
# 单房间验证：追加 --rooms living_room
& $python scripts/house_demo.py serve --output $out --port 8092
```

打开 `http://127.0.0.1:8092/`。本地服务器只监听回环地址，无需启动数据库或真实扫描 worker。
停止该命令即可停止展示服务。不要将无鉴权演示服务器暴露到公网。

## 产物与展示

- 每房间 `.pcd` / `.ply`：含地板、完整墙面、天花板、门框及简化家具；模型读取 PLY。
- `house_complete.pcd / .ply`：拼接后整屋。
- 每房间 `.bin`：浏览器用小端 float32 XYZRGB，6 个数/点；`.layers.bin` 为 uint8 结构/天花板/家具标记。
- `manifest.json`：坐标、门洞、点数和房间资源。
- `ground_truth.json`：**预设生成真值**，不能称为模型检测结果。
- `model/<room>/layout.txt / .json`：原始 SpatialLM 产物，保留不改。
- `predictions.json`：真实模型解析对象、每间状态和错误；无识别结果不会填入真值。

家具输出字段：`object / position / size / rotation / rotation_unit / source / room_id / instance_id`。
`position` 是世界坐标包围框中心，`rotation` 是绕 Z 轴的度数。
原生 SpatialLM Bbox 的 `angle_z` 为弧度，旧适配层字段虽然叫 `rotation_z_deg`，却未做转换。
本 Demo 从原始 layout 按类别、中心、尺寸追溯角度，再转度；不改旧适配层或原始文件。
匹配失败会报告失败，不根据角度大小猜单位。

页面默认显示模型结果；黄色“生成真值”模式需要手动选择且显著标注。
房间、着色、天花板、墙体/地板均可切换；可自由旋转、缩放、平移。
解析采用命令行触发，网页仅刷新已有结果，不提供未经鉴权的模型启动入口。

## 2026-10-12 本机实际验证

独立房间点数：客厅 52,942；卧室 38,158；厨房 26,658；卫生间 16,220；走廊 31,032。
完整房屋去重后 143,168 点。PCD/PLY 均用 Open3D 读回验证。

四房间真实推理成功退出，共约 194 秒（不含文件生成与适配开销），返回 7 个家具对象：

| 房间 | 耗时(s) | 原始模型类别 |
|---|---:|---|
| 客厅 | 37.2 | sofa、tv、coffee_table |
| 卧室 | 39.8 | sofa、cupboard |
| 厨房 | 37.4 | coffee_table、dining_chair |
| 卫生间 | 79.4 | 无家具对象，识别出墙/门结构 |

**这不是识别全正确的验收**：床被误识别成 sofa，客厅椅子、厨房柜、卫生间柜漏检；
厨房餐桌被识别为 coffee_table。仍保留这些实际输出，不按生成真值改写类别。
合成家具使用简化几何，真实感有限；后续若需更高识别率，应使用有授权的高质量家具几何资产，
而不是硬编码检测标签。可展示完整数据流，但不能用于宣称真实房屋识别精度。

运行中采样到显存约 3.4 GB（非持续监测峰值），未出现 CUDA OOM；结束后读数约 12 MiB。
浏览器采用本地 Edge WebGL 软件渲染做独立验收，加载、切房间、颜色、标签、天花板和结果刷新均通过，
未出现 pageerror。真实生成模型与 UI 端到端链路已验证。

测试使用隔离 SQLite 数据库及 E 盘临时目录；不改生产数据库。
执行 `pytest tests/test_house_demo.py tests/test_structure_builder.py tests/test_preview_selection.py`，
另执行现有 `node tests/test_preview_chunks.cjs`；最终数量见本次执行记录。

未执行 commit / push，未删除或改写已有业务文件，未合并此前 Scan46 实验修复。

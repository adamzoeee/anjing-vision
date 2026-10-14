# 家庭点云 Demo：室内语义物体增强

本轮是**程序化合成场景增强**，不是照片级重建或识别精度达标声明。
不修改真实扫描链路、SpatialLM 权重/算法/参数、API 或数据库。

## 现状分析与改动范围

原生成入口为 `pipeline/house_demo.py`：`ROOMS / PORTALS` 定义布局，
`generate_room()` 采样墙地顶和家具；`build_demo()` 导出 PCD/PLY；
`infer_rooms()` 调用原有 `spatiallm_runner.run_inference()`。
原家具主要是方盒，床/沙发几何相似；无窗洞、窗帘和洁具结构。

保留全部函数签名、米制 Z-up 坐标、房间 origin/size、0.9 × 2.1 m 门洞和模型调用。
现有 `furniture_parts()` 仍作为简单家具后备生成器；新增资产模块只负责几何采样。

本轮新增：

- `backend/pipeline/house_demo_assets.py`：物体配置、外墙窗洞、确定性曲面及多部件表面点云。
- `backend/tests/test_house_demo_assets.py`：分类覆盖、坐标、窗洞、水槽开孔、床结构、窗帘和马桶测试。
- 本说明。

本轮修改：

- `backend/pipeline/house_demo.py`：组合新增几何、挂装高度、窗口裁剪和可视化点索引范围。
- `backend/app/static/house_demo/index.html`、`demo.js`：房间名、点击选房、分类颜色、半墙观察和标签避让。

## 场景内容与方法

| 房间 | 物体数 | 内容 |
|---|---:|---|
| 客厅 | 10 | 沙发、茶几、电视、椅子、电视柜、空调、地毯、绿植、窗户、窗帘 |
| 卧室 | 9 | 床、衣柜、床头柜、书桌、椅子、台灯、空调、窗户、窗帘 |
| 厨房 | 8 | 连续橱柜操作台、桌、椅、水槽、冰箱、灶台、抽油烟机、窗户 |
| 卫生间 | 6 | 洗手台、马桶、镜子、淋浴区域、花洒、毛巾架 |
| 走廊 | 3 | 鞋柜、墙面装饰、顶灯 |

窗户位于外墙，并裁掉实际窗洞墙点；框架与玻璃都有点云。
窗帘由两个打开的褶皱曲面和悬挂杆组成。床板、床垫、床头、枕头分别采样，
床垫顶约 0.50 m，床头最高 1.0 m；床占地 1.8 × 2.0 m。
水槽挖去操作台对应表面；马桶含底座、开放坐垫区域、曲面盆及水箱。
小型曲面局部约 2.5 cm 采样，墙面仍按输入 spacing（默认 5 cm）。没有随机散点或照片贴片。

模型读取自然配色的 XYZRGB PLY；类别着色在浏览器端根据单独保存的生成真值完成，
不向模型输入类别标签或预设家具 JSON。`ground_truth.json` 新增 `label / point_range` 字段，
旧字段保留；`generate_room()` 仍返回原四元组。

半墙观察是 WebGL 裁剪，完整输出点云和模型输入不裁剪；天花板开关仍有效。
房间名称来自预设布局，不是模型自动判断。模型模式与黄色生成真值模式始终分开。
标签靠近时仅隐藏重叠文字，侧栏仍显示全部物体及尺寸；不删模型结果。

## 运行与回退

增强版实际数据目录：
`E:\anlingzhijing\anjing-vision-remote-latest\backend\data\demos\house_demo_rich_20261012`

增强版：`http://127.0.0.1:8093/`

原版：`http://127.0.0.1:8092/`（原目录和页面文件未替换）

在 backend 目录使用原 CLI；新生成必须选择空目录：

```powershell
$python='E:\anlingzhijing\anjing-vision\.venv\python.exe'
$out='E:\anlingzhijing\anjing-vision-remote-latest\backend\data\demos\house_demo_rich_20261012'
& $python scripts/house_demo.py generate --output $out
$env:SPATIALLM_DIR='E:\.PJs\spatiallm'
$env:SPATIALLM_PYTHON='E:\xinkeyan\miniconda\envs\spatiallm\python.exe'
$env:SPATIALLM_MODEL_PATH='E:\.PJs\models\SpatialLM1.1-Qwen-0.5B'
& $python scripts/house_demo.py infer --output $out --rooms living_room bedroom kitchen bathroom corridor
& $python scripts/house_demo.py serve --output $out --port 8093
```

默认 infer 仍保留原行为（不含走廊），上例显式包含走廊。全部串行运行，没有多模型并发。
原版源文件回退副本：`E:\anlingzhijing\anjing-vision\_codex_stage\home_rich\before`。
源代码恢复与切换数据是不同操作；展示回退直接访问原版 8092 即可，不需要删除新数据。

## 2026-10-12 实际结果

点数：客厅 68,809，卧室 60,630，厨房 35,118，卫生间 21,369，走廊 35,051。
整屋去重后 **197,823 点**（原版 143,168，增加约 38%）。

| 房间 | 推理秒数 | 输出数量 | 原始模型类别 |
|---|---:|---:|---|
| 客厅 | 38.07 | 5 | curtain, sofa, carpet, coffee_table, air_conditioner |
| 卧室 | 35.29 | 5 | bed, desk, chair, curtain, nightstand |
| 厨房 | 35.44 | 6 | stove, range_hood, side_table, chair, refrigerator, painting |
| 卫生间 | 36.55 | 3 | toilet, hand_sink, shower_room |
| 走廊 | 37.22 | 0 | 无家具输出 |

五次真实调用成功，总计约 **182.6 秒**，输出 **19 个对象**；数量不等于正确识别数量。
床从旧版 sofa 变为 bed；卫生间从没有家具输出变为马桶/洗手台/淋浴区域。
仍存在问题：客厅电视本轮漏检；卧室衣柜等漏检；厨房返回 painting，需要视为可疑分类，
不能当作正确识别；走廊新增物体均未被输出。没有用生成真值补齐、改名或删除这些模型结果。
丰富几何提高了部分语义辨识，但不能保证所有类别被当前模型支持或准确识别。

没有 CUDA OOM 或模型调用异常；未进行全程显存峰值采样，不承诺任意密度都适配 8 GB。
不建议直接把整屋最高密度文件送入模型；继续按房间串行处理。

已完成：几何及模型适配测试、PCD/PLY 读回、原有预览相关回归、Edge WebGL 实际截图检查、
房间名称点击、五房间切换、真值/模型模式、半墙/天花板和分类着色检查。
最终通过数量以本轮 pytest 输出为准；未重跑所有不相关后端测试。

不变性核对 SHA256：

- 原 `spatiallm_runner.py`：`5E0111248C0CC225019313AD008F667BB39AD21120C81443531737B8A4DAAF96`
- 原版 `house_demo_20261012/manifest.json`：`6EA4A565A979533852EC4B1F505953A32248EEC2D50053D96C2EF8840DB0CB28`

本轮没有 commit / push，没有覆盖 Scan46 或其他真实扫描数据。

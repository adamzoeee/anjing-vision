# 生活化家庭点云与空间助手演示

## 本轮边界

在 rich-v2 上增量增加 lived-v3，不重构真实扫描、模型、API 或正式风险评估。
程序化家具几何仍为模拟数据，不是实拍。主要物体有计划地偏移和旋转，杂物有支撑面，
不使用随机散点。原 ROOM/PORTAL 坐标、门洞 0.9 × 2.1 m、PCD/PLY 格式、函数签名与
`spatiallm_runner.run_inference` 均保留。

## 配置与数量

使用 `generate --profile lived` 生成新场景；默认 `--profile rich` 继续生成上一版。
内部环境项 `HOUSE_DEMO_PROFILE` 仅在生成命令子进程内选择配置，不写入生产 `.env`。

| 房间 | 主要物体 | 附属物体 | 有意设计的居住痕迹 |
|---|---:|---:|---|
| 客厅 | 8 | 2 | 偏移旋转茶几、沙发旁小桌，茶几上的书和杯子 |
| 卧室 | 8 | 2 | 椅子未归位、床偏置、桌面书和杯子 |
| 厨房 | 8 | 1 | 餐桌转角、偏移椅子；窗户作为附属结构保留 |
| 卫生间 | 8 | 0 | 加入洗衣机及储物柜，入口附近轻度占用 |
| 走廊 | 6 | 0 | 衣帽架、伞架和柜旁收纳箱，仍保留通道 |

合计 38 件主要物体、5 件附属物体。厨房橱柜和操作台为一个合法装配组合，分别计数；
水槽安装在操作台开孔中。马桶/花洒/淋浴区等由已有真实表面采样器生成。
穿插检查排除明确的安装组合和上下支撑接触，不允许普通家具任意相交。

## 文件

新增：

- `backend/pipeline/house_demo_lived.py`：独立生活化摆放配置及明确活动目标点。
- `backend/pipeline/house_demo_layout.py`：两套证据的距离、关系、入口、路径及演示建议。
- `backend/tests/test_house_demo_lived.py`：数量、碰撞、支撑、可达性及证据来源测试。
- 本文档。

增量修改：`house_demo.py`、`house_demo_assets.py`、命令行 `scripts/house_demo.py`，
以及 `app/static/house_demo/index.html` 和 `demo.js`。原版函数和配置仍可用。

## 布局证据与建议

新增两个独立产物：

- `layout_analysis.json`：只依据生成场景的已知几何。
- `model_layout_analysis.json`：只依据真正的 SpatialLM 家具输出；房间边界、门口和活动点取自合成布局。

包含：地面家具旋转矩形间距、物体 ID、平面位置差、门内 1 m 检查区占用/剩余面积、
门到指定活动点的路线、沿路最小净空估计、建议引用的物体与数值。
小物品、台上物和高位壁挂物不误算为地面家具占地。

复用 `space_foundation` 已有旋转框距离、栅格化、A* 计算，不改变这些函数。
使用 4 cm 栅格，保留门外短延伸区域避免把门口净空误算为到外边界的距离，
家具占用不会因门洞开启而被清除；保守膨胀一格防止点接触角缝穿行。
路线宽度是沿所选路径的两倍障碍距离，并以门宽封顶，**不是所有可选路径的全局最大净宽**。
家具间距也不等于实际通道宽度。未模拟人体运动或门扇开启。

页面“小助手”是可追溯的**确定性几何规则演示**，没有新增或冒称调用大语言模型。
近家具提醒采用 0.30 m、路线提醒采用 0.65 m 的演示参数，不是法规/适老安全认证门槛。
不会生成安全评分。没有命中建议、模型无结果或漏检都不意味着安全。
不将生成真值补入模型结果，双来源由用户显式切换。

## 实际运行（2026-10-13）

生活化数据目录：
`E:\anlingzhijing\anjing-vision-remote-latest\backend\data\demos\house_demo_lived_20261013`

新演示地址：`http://127.0.0.1:8094/`。旧数据目录和 8092/8093 对应页面文件未替换。
生成整屋 **199,366 点**。

生成几何示例：

- 沙发—茶几约 0.17 m；沙发—小桌约 0.14 m。
- 床—椅子约 0.29 m。
- 卫生间所选路线局部净空约 0.56 m，门口检查区占用约 0.13 m²。
- 厨房所选路线约 0.79 m，本轮没有触发路线狭窄演示提醒；没有强行编造风险。

五空间真实 SpatialLM 串行调用成功，共约 **128 秒**：

| 房间 | 秒数 | 输出数量 | 原始类别 |
|---|---:|---:|---|
| 客厅 | 20.87 | 4 | curtain, sofa, coffee_table, coffee_table |
| 卧室 | 20.40 | 5 | bed, curtain, nightstand, coffee_table, chair |
| 厨房 | 21.16 | 5 | chair, cupboard, side_table, refrigerator, painting |
| 卫生间 | 45.04 | 4 | washing_machine, toilet, hand_sink, shower_room |
| 走廊 | 20.54 | 1 | clothes_rack |

输出共 19 件，不等于 19 件都准确。电视/衣柜等漏检、桌类细分类错误、厨房 painting 可疑分类仍在，
未用真值修饰。模型几何产生的客厅近沙发间距为约 0.16/0.24 m，与生成几何有差别；
卫生间模型路线约 0.64 m，高于生成几何的 0.56 m，体现漏检储物柜等会高估净空。

实际运行无 CUDA OOM；一次推理中采样约 3 GB 显存，非全程峰值统计。
测试覆盖旧 rich 配置与新 lived 配置、点云读回、实例支撑与碰撞、门洞、路径、未知非安全，
以及移动家具后建议是否改变。浏览器验证五空间、真实模型刷新、证据切换和数值建议均通过。

## 复现

在 backend 目录使用已有环境：

```powershell
$py='E:\anlingzhijing\anjing-vision\.venv\python.exe'
$out='E:\anlingzhijing\anjing-vision-remote-latest\backend\data\demos\house_demo_lived_20261013'
& $py scripts/house_demo.py generate --profile lived --output $out
$env:SPATIALLM_DIR='E:\.PJs\spatiallm'
$env:SPATIALLM_PYTHON='E:\xinkeyan\miniconda\envs\spatiallm\python.exe'
$env:SPATIALLM_MODEL_PATH='E:\.PJs\models\SpatialLM1.1-Qwen-0.5B'
& $py scripts/house_demo.py infer --output $out --rooms living_room bedroom kitchen bathroom corridor
& $py scripts/house_demo.py serve --output $out --port 8094
```

生成要求空目录，已有数据不要再次运行 generate 覆盖。只读展示可直接运行 serve。
回退源码副本在 `E:\anlingzhijing\anjing-vision\_codex_stage\home_lived\before`，
上一版展示数据完全保留。本轮没有 commit / push，没有改真实扫描。

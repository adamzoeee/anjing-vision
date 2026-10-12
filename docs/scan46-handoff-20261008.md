# 扫描46精简交接（2026-10-08）

## 交付基准

复现的是当前完整前端中的**扫描46**：`window_wall_joined_20261008.ply` + 配套 `.visual.json` 的六个展示层 + 结构、尺寸、通道和评分数据。不是诊断网页，也不是重新训练出来的近似结果。

默认从房间上方看向内部，可旋转、缩放、平移、调点大小、切换空间结构。默认点大小3.75；若对照用户调大后的截图，改为4.25。镜头的临时拖动状态不属于数据集。

窗墙采用约1.5°的局部校正量；**这不是宣称最终残余倾斜为1.5°**。窗帘和床头已改善，但书架靠窗侧仍黑碎、窗框旁仍有裂缝，未宣称完美修复。

## 数据包对照

| 包 | 作用 |
|---|---|
| `scan46_最新结果补充包(2).zip` | 旧运行结果及评分、通道、报告；只复用与当前内容一致的文件 |
| `scan46_窗户增量补充包(2).zip` | 旧窗边组件及来源索引；不是最终正式选择 |
| `scan46_训练与补拍研究增量包_20260921.zip` | 原始补拍、window2点图/帧和旧研究；恢复脚本只取有用条目，不恢复失败候选 |
| `scan40_数据包.zip` | 另一轮扫描，作为独立参考；**不能解压为扫描46底包** |
| `scan46_当前前端精简增量_20261008.zip` | 本次新增：正式点云、六层完整表面/纹理、缺少的业务数据、扫描46主视频、最小数据库快照及清单 |

补拍已经在研究包的 `data/training_sources/scan46_buchong2/`，不重复打包。窗户修改优先看：

- `窗户边角2.mp4`、`窗户边角.mp4`：窗帘、窗框、床头与书架交界。
- `窗户那面墙/窗户那面.mp4`、`窗户那面墙/1000045072.mp4`：窗墙与固定结构。
- `书架上面.mp4`、`书架上面2.mp4`、`橙色书架/1000044794.mp4`：核对书架身份和遮挡。

手机为vivo S30，1×，无变焦、无超级防抖。主视频在新包 `data/media/46/1000044487.mp4`。这批素材不含激光/传感器实测深度；模型点图不能当成绝对真值。

## 恢复步骤（不训练、不重新生成点云）

从 [GitHub main](https://github.com/adamzoeee/anjing-vision) 下载当前代码，用**新的目录**运行。安装后端依赖，进入 `backend`。三个旧扫描46 ZIP 和新ZIP无需手动全部解压：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/scan46_delivery.py restore `
  --bundle "新包路径/scan46_当前前端精简增量_20261008.zip" `
  --previous "旧包路径/scan46_最新结果补充包(2).zip" "旧包路径/scan46_窗户增量补充包(2).zip" "旧包路径/scan46_训练与补拍研究增量包_20260921.zip"
```

脚本先校验清单中的每个文件，再恢复指定条目；新包最后应用，保留修改时间用于预览缓存版本。生成 `scan46-handoff.db`，只带该扫描的项目和报告，不带真实账号密码、密钥及其他历史任务。图片路径按新机器重建。已有不同数据/数据库时会拒绝覆盖，请换新目录。

在同一PowerShell窗口启动**本地验收环境**：

```powershell
$env:ENVIRONMENT = 'development'
$env:DATABASE_URL = 'sqlite:///./scan46-handoff.db'
$env:DATA_DIR = './data'
$env:SECRET_KEY = (python -c "import secrets; print(secrets.token_hex(32))")
$env:DEMO_LOGIN = 'true'
$env:DEMO_LOGIN_EMAIL = 'demo@demo.com'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另一个窗口进入仓库 `app`：

```powershell
flutter pub get
flutter run -d web-server --web-hostname 127.0.0.1 --web-port 3000 --dart-define=DEMO_LOGIN=true --dart-define=API_BASE_URL=http://127.0.0.1:8000
```

打开 `http://127.0.0.1:3000`，演示登录，进入“我的全黑家”扫描46的3D预览。不要复用旧JWT链接。演示登录仅供本机验收，**不得用这些设置公开部署**。线上使用接手方自己的认证、数据库和密钥。

## 接手修改重点

先保留交付基准，在独立候选中修改。重点是**书架—窗帘中段交界、窗框黑缝**，不要重新破坏已改善的床头、盒子/袋子和窗帘下缘。

1. 用相同正视/斜视/俯视核对：当前 `.visual.json` 的六层是否重复显示、窗帘右缘三角形是否错误遮挡书架；先做仅关闭可疑边面、不加点的A/B对照。
2. 查真实RGB、点图和构面索引，确认对象身份、深度跳变和边长。只撤掉有证据的跨物体三角面；不能把窗帘切直、拉宽或拉到书架。
3. 若书架确实缺表面，分侧板/层板/背板，用已有同板面定位、真实视频边界限定覆盖，不能用任意大平面填洞。SLAM3R不同裁剪曾产生深度偏差，不能直接叠成第二层书架。
4. 剩余窗框缝只处理确认误裁的真实折边、重复层和局部映射；不再启动全屋BA、标记打印或普通补拍循环。确实缺独立几何证据时明确指出具体面，不伪造。

验收：窗帘保持褶皱、不双层、不像厚贴片；书架侧更完整且仍是同一刚体；窗框缝缩小；床头不新增黑带；四视角没有新的漂浮、灰面或断层。程序运行成功不代表视觉达标。

无需模型权重、GPU或训练即可复现本次成品。若重新训练，结果不保证逐点一致；应先完整复现交付结果再修改。

## 大屏查看器坐标系校正（2026-10-09 修复，2026-10-09 晚间修正）

现象：大屏 `#/scans/46` 家具框已正确，但点云在水平面上与框整体错开（“书架框在北、点云的
书架在东，像转了 90°”），修完旋转后又变成“靠门那侧框架把点云整个包住、所有物品框都不在
对应位置”。根因有两层，都不是交付数据错误：

1. 点云逐点映射漏掉了水平面对齐（只有窗边展示层走了 `framePoint`）；
2. 水平面变换本身是**按点云外接包络**解的，而包络被伸出房间的几何撑大：门口走廊在
   包内一直延伸到 z≈2.44（比门墙远 0.43×1.19≈0.5 m），穿窗几何同样在窗墙之外。
   用包络角点去对房间轮廓，整包点云会朝门那一侧推偏约 0.5 m，家具框因此全部错位。

交付包 `window_wall_joined_20261008` 的坐标事实（只读复核）：

- 竖直轴是包内 Y、up = −Y：地板面在 y≈1.345（`modalPlane` 取直方图最密的一层），
  `views.front.R[1]=(0,1,0)`、`views.*.C` 的高度 1.3~1.4 都一致；窗框/窗帘/床沿补片
  的形状也只有按“y 为高度”才成立。
- 水平面绕竖轴 180°：包内 z 最小一侧是**窗墙**（z≈−0.27，窗洞 x 0.56~1.75），最大一侧是
  **门墙**（z≈2.01，门洞两侧墙面色阶明显，走廊地板在 z≈2.07~2.44）；包内 x 最小一侧是
  床头/挂画那面。只有 180°（包内最大角对准房间最小角）才能让门扇、窗洞、床同时落进
  `structure_calibrated.json` 审计过的门框、窗框、`bed_001`。
- **非度量**：包是 SLAM 重建，两条水平轴尺度不同——墙面间距 2.44（宽）×2.27（进深）对
  审计房间 2.894×3.270，得出**宽度 1.187、进深 1.433**；竖直方向与宽度同尺度（1.187：
  门框高 1.85→2.20 对审计 2.10 差 4%，床垫短边 1.0→1.19 对审计床宽 1.47 偏窄但方向
  一致，床的长边、窗帘/窗框 mesh 在深度上按 1.433 展开后与审计尺寸吻合）。焦距类误差
  只压缩深度方向，所以竖直方向取“未被压缩”的那条水平轴尺度。
- 地板锚点必须用 `modalPlane`：99.5 分位会落在平面之外的离群点上，把整包点云抬高约 13cm。

修改点：`backend/app/static/console/js/viewer/frame.mjs`（新增 `wallPeaks` 墙面剖面与
`fitWallFrame` 逐轴墙面拟合；`fitHorizontalFrame` 包络拟合降级为拿不到剖面时的兜底；
`framePointInto` 支持 `scaleX/scaleZ/scaleUp`，仍是点云与展示层唯一共用实现；`modalPlane`
与 `packUpAxis` 不变）与 `pointcloud.js`（水平极值与竖直中段 x/z 剖面在同一次扫描里采样，
地板 → 剖面 → `resolveFrame`，状态栏显示是否已校正）。

验收（`backend/tests/test_console_frame_fit.cjs` 走真实交付包，全部 ±2cm 级断言）：
四道墙面映射后落在房间轮廓上（误差 <2cm）；门口走廊 100% 落在房间线框**之外**；
门扇质心 y=−0.51 正落在审计门墙上、床铺质心落在 `bed_001` 内、窗洞 x 覆盖审计窗中心；
6/6 家具框落在点云上（只修竖直轴 4/6）。node 6 个脚本 + `pytest -q` 390 passed。

已知残差：包内穿窗、门口走廊的几何本来就在房间之外，映射后仍在房间线框外（这正是
“框架贴合现实、不把点云整个包住”的预期行为）；交付包是 visual-only 资产
（`.visual.json` 里 `excluded_from_measurements=true`），所有尺寸仍以
`structure_calibrated.json` / `measurements.json` 为准，不要把点云当量测真值。

## 2026-10-09 更新：整体框架校准参数固化（用户手调）

上一节的口径（按墙面剖面把**点云**逐轴缩放拟合进房间轮廓）仍会被用户读成“点云被拉伸、
框架还是不对”。用户改用校准页 `console/calibrate.html` 手动拉参数，定稿口径变成：

**点云保持交付包原生尺寸**（`p_display = (pack_x, pack_z, floor − pack_y)`，不翻转、不缩放、
不做水平拟合）；**整个结构大框架**（墙/门/窗/家具框 + 标签，全部一起）按
`p_display = T · S · Rz(yaw) · p_business` 变换贴到点云上，不允许单独挪某个物品框。

定稿参数（`scan46-frame-calibration.json`，贴合分 169734）：

| 项 | 值 |
|---|---|
| yaw / mirror | 180° / 无镜像 |
| scale | X 0.84、Y 0.71、Z 0.9 |
| translate | X 1.54、Y 1.64、Z 0 |
| 地板锚点 floor_y_pack | 1.3449620543191287 |

落地方式：

- 参数文件放在扫描数据目录 `backend/data/work/46/postprocess/frame_calibration.json`
  （仓库内另存版本化副本 `docs/scan46-frame-calibration-20261009.json`）。
- 清单 `manifest.json` 新增可选字段 `frame_calibration`（文件存在才给出），
  路由 `GET /api/preview/{scan_id}/frame_calibration.json`（no-store）。
- `frame.mjs` 新增 `calibrationTransform / calibrationPoint / calibrationRows /
  resolveCalibration`：参数不完整、或包内视角与“up = −Y、地板在最大 y 一侧”的约定
  不符时整体拒绝，绝不半套用；地板锚点优先用校准文件记录值（与手调时逐位一致）。
- `pointcloud.js`：有校准文件时点云与窗边展示层只做竖直轴映射（`(x, z, floor − y)`），
  结构框与标签所在的 `boxesGroup` / `labelGroup` 套同一个 `T · S · Rz(yaw)` 矩阵；
  没有校准文件的扫描继续走上一节的墙面剖面通用拟合，行为不变。

验收：`backend/tests/test_console_frame_calibration.cjs` 用真实交付包复算贴合分 =
169734（与手调记录逐点一致），6 件家具框逐框命中（最少 4303 点），点云显示外接框与
手调记录一致（原生尺寸）；`backend/tests/test_preview_frame_calibration.py` 守住清单字段
与 404 回退。参数若重调，用校准页重新导出并覆盖 `frame_calibration.json` 即可，不需要
重新训练或移动任何家具框。

### 视图模式口径（同日追加）

“真实场景”只渲染点云与窗边展示层；结构框架（墙/门/窗/家具）与标签只在“空间结构”里
渲染，结构模式同时隐藏点云、启用结构网格。规则集中在
`backend/app/static/console/js/viewer/mode.mjs` 的 `layerVisibility()`（真实场景、
未知模式一律不画框架），`room.js` / `screen.js` 在切换模式时把框架图层 chip 降调并注明
“框架仅在‘空间结构’视图渲染”；`backend/tests/test_console_mode.cjs` 守住这条口径。

### AI 改造助手入口（2026-10-12 追加）

“AI 改造模拟 + 方案库”面板一直位于房间工作台右栏底部；1080p 下它在折叠线以下，右栏本身
可滚动（实测 clientHeight 878 / scrollHeight 2284），因此容易被误认为“功能不见了”。
现在房间页顶部操作区新增“AI 改造助手”按钮：点击后右栏平滑滚动到该面板并播放一次高亮
脉冲（`jumpToAssistant`；样式 `.panel.assistant-flash`）。实测点击后右栏 scrollTop
0 → 1121、面板入视且高亮生效。

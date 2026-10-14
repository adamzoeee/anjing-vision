# 家庭三维渲染与 C4D 导出

## 定位与数据来源

这是独立的程序化场景复现，不是从手机视频扫描恢复出的真实纹理网格。原点云页面和数据保持不变。

- 输入：`backend/data/demos/house_demo_risk_20261013/`。
- `manifest.json`：五个房间坐标、尺寸、0.9 × 2.1 m 门洞。
- `ground_truth.json`：完整生成布局，45 个对象（包含小物品）。默认渲染使用此来源。
- `predictions.json`：真实 SpatialLM 输出。`--mode model` 可单独输出模型布局，禁止真值补漏。
- 原 `.bin` 点云做长度完整性校验，保留输入路径；`render_spec.json` 记录布局文件 SHA256。

流程：已有结构/家具数据 → 独立 render_spec → Blender 参数化实体 → 材质/灯光/相机 → 渲染图 + FBX/GLB/glTF/OBJ。

## 文件与运行

新增脚本（不修改旧 Demo 接口）：

1. `backend/scripts/house_render_prepare.py`：校验来源、划分共享墙面、保留门窗洞。
2. `backend/scripts/house_render_blender.py`：分部件家具、基础材质、场景层级、离线渲染与导出。
3. `backend/scripts/house_render_verify.py`：FBX/GLB/OBJ 导出回读、尺寸边界和相机等校验。
4. `backend/tests/test_house_render.py`：门窗洞、重复墙面、变换、来源和失败处理测试。
5. `backend/app/static/house_render/`：独立预览页面；Three.js 和 GLTFLoader 为 MIT 许可。

Blender 4.5.3 官方便携版位于 E 盘 `anjing-vision/tools/blender-portable`，不修改生产环境。

```powershell
# 在 anjing-vision-remote-latest 根目录，以实际 Python/Blender 路径执行。
python backend/scripts/house_render_prepare.py --source backend/data/demos/house_demo_risk_20261013 --output backend/data/demos/house_render_20261014
blender --background --factory-startup --python backend/scripts/house_render_blender.py -- --spec backend/data/demos/house_render_20261014/render_spec.json --samples 24 --resolution 1200
blender --background --factory-startup --python backend/scripts/house_render_verify.py -- backend/data/demos/house_render_20261014
```

输出目录必须是新的，脚本不覆盖原点云。单独模型来源另选输出目录并指定 `--mode model`。

## 建模约定

米制、Z-up；家具根节点保留中心对应的底部位置、yaw 和尺寸缩放。实体由床架/床垫/枕头、沙发座垫/扶手、柜门/把手、马桶开口盆体等组成，未知类别保留显式 proxy 标记。

结构层级：house → 房间 → furniture_房间 → 分类_instance_id → 几何部件；另有 lights、cameras、door_frame。共享墙只构建一次，附带所属房间列表。

有完整墙体、天花板及窗洞。近侧墙面/天花板仅在渲染时临时隐藏。原点云没有门扇开启证据，隐藏的可选门扇仅留在 `.blend` 中，默认不导出，避免凭空堵门。窗帘为起伏网格，不是贴图。

## 导出格式选择

- **FBX**：优先用于 C4D 编辑，保留对象层级、基础材质、相机和格式支持的灯光。导入后仍应复核灯光强度和材质。
- **GLB**：便于打包和网页预览；另提供 `.gltf` + `.bin` 以兼容较旧导入器。不要只拷贝 `.gltf` 而漏掉 `.bin`。
- **OBJ + MTL**：静态几何后备格式，无相机/灯光和完整父子变换语义，两文件放在同一目录。
- **BLEND**：完整可编辑主工程，保留程序化细节、照明和可选门扇。未生成 Alembic。

Blender 主工程 Z-up；交换格式转换为 Y-up，由导入器还原，**不要再次手工旋转 90°**。OBJ 无单位元数据，导入时设为米。若 C4D 工程用厘米，1 m 应显示为 100 cm；用门宽 0.90 m、门高 2.10 m 和客厅 6 × 4 m 复核，避免二次缩放。

## C4D 操作

1. 新建工程，通过“合并/打开”导入 `house.fbx`，保留材质、相机、灯光和自动单位转换。
2. 在对象管理器展开 house，按房间编辑家具；总览可隐藏 ceiling，内部相机查看时启用完整墙体。
3. 检查门洞尺标和地面方向，再设置渲染器。需要时把材质转换为 Redshift 标准材质。
4. 使用 camera_overview 或各房间相机，调整灯光曝光，再出图/制作相机动画。

本机未发现 C4D，验证范围为 Blender 导出回读和网页，不声称已完成 C4D 软件内验收。

## 2026-10-14 最终交付与验收

- 最终产物：`backend/data/demos/house_render_20261014_final/`。
- 独立渲染版：http://127.0.0.1:8096/ ，原点云版 http://127.0.0.1:8095/ 保留。
- 45 个输入对象、61 个去重墙面单元、391 个导出网格、21 种使用中的材质、6 个相机。
- 6 张 1200 × 900 PNG：overview、living_room、bedroom、kitchen、bathroom、corridor。
- 65 项相关 pytest 通过，preview chunks 通过；不是整个项目全量回归。
- FBX/GLB/OBJ 均实际 Blender 回读通过。FBX/GLB 的 45 个家具根节点完整变换逐一一致（容差 1e-4）；整体边界最大偏差约 0.00000122 m。
- FBX：391 网格、21 材质、6 相机、11 灯。GLB：391 网格、21 材质、6 相机、5 点光源（区域光不保留）。OBJ：391 网格、21 材质，无灯光/相机。
- 浏览器实际 GLB 加载、六视角切换、剖墙/天花板开关、图片加载和所有下载链接通过，无 pageerror。
- 自动按房间包围范围取景；分房间隐藏其他房间门框。图片完成目视检查。
- 所有最终文件位于 E 盘。初次相对渲染路径误写的 PNG 已移回 E 盘，源码已改为绝对路径。
- 下载包经官方 SHA256 校验：`6b657c8bdd3a7b65b07b9e1ae17eb4be7dd4aa23121da7f3d3354fc2551330a7`。
- 仅新增模块，原业务/点云代码未修改，无 commit/push。

浏览器预览服务器重启：

```text
python -m http.server 8096 --bind 127.0.0.1 --directory backend/data/demos/house_render_20261014_final
```

完整新增文件清单：上述三个 scripts、一个 test、`backend/app/static/house_render/index.html`、`render-viewer.js`、`GLTFLoader.js`、本说明，共 8 个源码/文档文件。产物和便携运行环境不纳入源码提交。

## 兼容性和限制

基础颜色/粗糙度/金属度优先保留；程序化木纹与布纹 bump 未烘焙成贴图，跨软件可能丢失。玻璃折射、镜面、色彩管理及光强不是跨渲染器像素一致的。GLB 使用点光补光；区域灯在不同导入器可能不支持，必要时在 C4D 重建。OBJ 不保留照明。

现有家具尺寸/布局不被反向修改；继承的空间问题是演示几何，不是正式住宅安全评估。程序化家具仅为代理模型，不承诺精细扫描准确度。

官方参考：
- https://docs.blender.org/manual/en/4.5/addons/import_export/scene_gltf2.html
- https://help.maxon.net/c4d/en-us/Content/html/FFBXIMPORT-FBXIMPORT_GRP_MATERIALS.html
- https://help.maxon.net/c4d/en-us/Content/html/FGLTFIMPORTER-GLTFIMPORTER_GROUP_SETTINGS.html
- https://support.maxon.net/hc/en-us/articles/10493489496604-Cinema-4D-2024-0-0-September-13-2023

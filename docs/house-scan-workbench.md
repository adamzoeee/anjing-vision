# 逐房间扫描与整屋拼接

在**同一项目**中逐个新建房间 Scan，上传各房间视频，并等待点云后处理与结构识别完成。项目页的“拼接整屋”列出已完成房间及识别到的门。勾选至少两个房间，给房间命名，将同一物理门在两份 Scan 中的门框逐组配对，然后点击“生成整屋 Scan”。系统会在这个项目中新增一份 `house` 类型 Scan；原房间 Scan 保留，可以继续扫描新房间，再生成下一版整屋 Scan。

门列表来自每个房间的 `structure.json` 或 `structure_calibrated.json`，使用稳定的 `door_01` 等编号。系统检查门位于房间轮廓边界、门宽高相近、同一扇门没有重复使用、门连接图覆盖所有所选房间，并根据配对门的中心和朝外方向求每个房间的水平旋转与三维平移。多个门组成闭环时，还会验证计算出的位姿一致。门对应关系和房间来源 Scan ID 保存到 `house_connections.json`。

拼接产物位于新 Scan 的 `data/work/{scan_id}/postprocess/`：

- `scene_preview.ply`：按房间位姿变换并合并的点云；
- `structure.json`：同一坐标系下的墙、门、窗及家具框架；
- `house_connections.json`：原房间 Scan、门对应关系和房间位姿。

新 Scan 从项目列表直接进入 `#/scans/{scan_id}` 工作台，可以切换点云、框架或叠加模式，按房间或门定位。预览沿用现有机构鉴权与 `/api/preview/{scan_id}/` 接口。整屋 Scan 当前是空间模型，不生成整屋风险评分。

接口：`GET /api/projects/{project_id}/scans/house/rooms` 获取可拼接房间及原因；`POST /api/projects/{project_id}/scans/house` 提交 `{ "scan_ids": [1, 2], "labels": { "1": "客厅", "2": "卧室" }, "links": [{ "scan_a": 1, "door_a": "door_01", "scan_b": 2, "door_b": "door_01" }] }`。新房间点云与结构须共用米制、Z 轴向上坐标；当前只支持同一楼层的房间。若旧扫描带有单独手调的 `frame_calibration.json`，房间列表会明确标为不可拼接；需先将其点云与结构固化到统一坐标。

仓库保留了原合成家庭 `house_demo` Scan 和导入接口用于回归验证；项目页的常规入口是逐房间拼接。

# P0 实时采集审计与实施设计

审计日期：2026-10-17。开发起点：main / `844ff7f9f818daf27a7447f57e7a60b440da2f2c`，工作区干净，origin/main 相同。远程为 adamzoeee/anjing-vision。队友 renovation-sandbox 的本机修改不在本次工作区，不覆盖或暂存。

## 发现

- FastAPI 入口 `backend/app/main.py`；Pydantic Settings 管理 `.env`；JWT、用户/机构范围在 `app/deps.py`；统一错误响应和请求日志已存在。
- Flutter 经 `app/lib/api/client.dart` 的 Dio 客户端携带 Bearer；图片可用 `Image.memory`，无需额外流媒体依赖。
- capture_page 的 image_picker 用于录制/选择后上传；没有跨模块实时 VideoFrame、共享 VideoCapture、有界帧队列、实时状态或视频预览协议。
- 已有任务后台线程/Celery 用于离线重建，不能作为实时帧积压队列。新采集不调度 Scan，不占用重建队列。
- requirements 已有 opencv-python、NumPy；本机后端 Python 3.12.13 / OpenCV 5.0.0。兼容 OpenCV 4/5，不升级依赖。
- 当前主编排：视频 → SLAM3R → 后处理/位姿 → SpatialLM → structure/measurements/passage → 正式风险/图/PDF。该链不接入或改写实时采集结果。
- 修改前后端全量基线：394 passed，1 个已存在的 ZIP 重复条目警告；无历史失败。

## 设计

独立 `backend/realtime/` 提供输入接口、帧结构、队列及采集管理；`app/routers/live_capture.py` 复用 JWT 与机构隔离；Flutter 增加一个项目页可进入的实时预览页面。

```text
唯一设备租约 → OpenCV 子进程（仅底层句柄）
  → 采集管理线程 → 有界、丢旧帧队列 → 后续推理消费者
                  └ 最新帧快照 → 鉴权 JPEG → Flutter
```

原生驱动 open/read 可能不返回，因此在可终止子进程中隔离；管理线程不执行模型、JPEG 编码或持久化视频。状态与生命周期使用锁和可中断等待。设备租约避免共享 DATA_DIR 的多个 API worker 同时打开同一 USB 编号；仍推荐单 API worker，以保持内存状态一致。

选择 JPEG 快照轮询：现有 Dio 可携带 Bearer 读取 bytes，Flutter/Web 共用；无新增 WebSocket、MJPEG 或视频服务器。预览读取独立快照，不偷走推理队列帧；关闭页面只停止 HTTP 轮询，不停止其他消费者。

文件由管理员服务器配置，不接收任意远端文件路径或 RTSP URL。状态及日志不泄露完整本地路径。默认禁用、无自动开机采集、无音频、无视频落盘。单会话机构所有权防止其他机构查看或控制正在采集的相机。

## 增量改动范围

1. 新增 realtime 输入/配置/帧结构及测试。
2. 新增队列、统计和生命周期管理及测试。
3. 修改 app/config.py、main.py 与 .env.example，新增 live_capture 路由及 API 测试。
4. Flutter 增加状态模型、API 方法、项目页入口、预览页面及测试。
5. 增加开发说明、实际验收报告与文档索引。

不更改数据库模型、重建适配、语义/实例/几何参数、尺度与风险规则，不修改真实 Scan46 数据。实体摄像头自动重连只能在可控假源中测试时必须明确标注，不能视为硬件拔插已验收。

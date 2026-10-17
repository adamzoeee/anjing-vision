# P0 统一实时视频采集

本模块是后续姿态、环境变化、材质识别共用的输入底座，**尚不包含这些模型或风险判断**。与离线视频上传、SLAM3R、SpatialLM、测量和报告链互不调用。默认 `REALTIME_ENABLED=false`，启动 API 不会打开摄像头。

## 目录与数据流

```text
backend/realtime/config.py       有界、可验证的源配置
backend/realtime/frame.py        只读 BGR VideoFrame
backend/realtime/source.py       VideoSource 协议、USB / 文件 OpenCV 实现
backend/realtime/queue.py        线程安全、有界、丢旧帧队列
backend/realtime/manager.py      会话、生命周期、统计及独立预览快照
backend/app/routers/live_capture.py  复用 JWT / 机构权限的 API
app/lib/api/live_capture.dart    前端状态数据
app/lib/pages/live_capture_page.dart  现有 Flutter 的预览页面
```

```text
USB 或本地文件 → 唯一设备租约 → OpenCV 子进程
  → 采集管理线程 → LatestFrameQueue → consume_latest() → 未来模型消费者
                 └ 最新快照 → 鉴权 JPEG → Dio bytes → Flutter Image.memory
```

原生驱动 open/read 可能阻塞：句柄隔离在可终止子进程，父线程用有限超时与取消信号等待。正常关闭尝试释放句柄；必要时终止的仅是自身采集子进程。JPEG 编码在请求线程，不在采集线程，无额外视频服务器、WebSocket 或 MJPEG 依赖。

## 安装和启动

沿用现有 Python 环境及 `backend/requirements.txt` / `requirements-dev.txt`，已有 opencv-python、NumPy、Pydantic、FastAPI，不需要新模型或 CUDA。验证过本机 Python 3.12.13 / OpenCV 5.0.0；代码兼容有/无 `cv2.setLogLevel` 的版本。

从仓库根目录运行：

```powershell
cd backend
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
# 按项目原有说明配置数据库、SECRET_KEY 等；不要覆盖个人 .env。
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

实时采集由 API 管理，不经过 Celery；仅使用该功能时不需要启动 GPU Worker。生产 API 的数据库、机构账号、安全配置要求仍然有效。

### 用本地视频模拟

在启动 API 的同一 PowerShell 设置环境变量，或将下列选项写入本机未提交的 `backend/.env`：

```powershell
$env:REALTIME_ENABLED='true'
$env:REALTIME__SOURCE_TYPE='file'
$env:REALTIME__VIDEO_PATH='C:/your-local-samples/test.avi'
$env:REALTIME__TARGET_FPS='10'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

路径由部署管理员配置，必须指向服务器已有文件。客户端不能提交任意文件路径或网络地址。按 `min(目标 FPS, 文件标称 FPS)` 的节奏依次读取、不补重复帧；目标低于源 FPS 时是慢速模拟，不是抽帧跳播。解码 PTS 不是可靠原始拍摄时刻。文件结束进入 `stopped / end_of_file`，不自动循环或重连。

### 使用 USB 摄像头

```powershell
$env:REALTIME_ENABLED='true'
$env:REALTIME__SOURCE_TYPE='usb'
$env:REALTIME__DEVICE_INDEX='0' # 示例默认设备；按部署机器实际编号调整
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

请允许操作系统摄像头权限，并关闭其他独占设备的程序。浏览器无需请求本地摄像头权限：采集的是 **API 所在机器**的设备，不是客户端手机。启动后仍需管理员点击“启动采集”，不会自动采集。

### 现有前端入口

```powershell
cd app
flutter run -d chrome --web-port 8080 --dart-define=API_BASE_URL=http://127.0.0.1:8000
```

登录 → 项目列表右上角摄像头按钮“实时视频预览” → 管理员启动采集。页面显示连接状态、实际分辨率、采集 FPS、队列、丢帧、重连和最近错误。状态约每秒查询一次，画面最多每 250 ms 请求一次；预览 FPS 不等于输入 FPS。

普通成员只能预览同机构会话。关闭预览、离开页面或应用转入后台会停止/取消 HTTP 请求，不停止其他消费者；管理员的“停止采集”才会停止共享源。Web 跨域仍遵循原 CORS 配置，非默认端口须加入 `CORS_ORIGINS`。

## 配置

`backend/app/config.py` 的 `Settings.realtime` 使用 `REALTIME__` 嵌套环境变量；样例在 `backend/.env.example`。修改配置需要重启 API，不支持客户端热改路径。

| 环境变量 | 默认值 | 含义 / 边界 |
|---|---|---|
| `REALTIME_ENABLED` | false | API 启动控制开关 |
| `REALTIME__CAMERA_ID` | camera_0 | 1–64 位字母数字、下划线、短横线 |
| `REALTIME__SOURCE_TYPE` | usb | usb / file；未实现 RTSP |
| `REALTIME__DEVICE_INDEX` | 0 | 通用默认编号；0–64 |
| `REALTIME__VIDEO_PATH` | null | file 时必填；不通过 API 返回完整路径 |
| `REALTIME__WIDTH` / `HEIGHT` | 640 / 480 | 请求/输出上界，16–1920 / 16–1080；只缩小、不拉伸 |
| `REALTIME__TARGET_FPS` | 10 | 大于 0、最多 60；实际以到达统计为准 |
| `REALTIME__QUEUE_CAPACITY` | 2 | 1–32；满时丢最旧待处理帧 |
| `REALTIME__RETRY_INTERVAL_S` | 0.5 | USB 重试基础间隔 |
| `REALTIME__RETRY_MAX_INTERVAL_S` | 4 | 指数退避上限，不小于基础间隔 |
| `REALTIME__RECONNECT_MAX_WAIT_S` | 15 | 每次连续故障恢复预算；有效帧才重置预算 |
| `REALTIME__IO_TIMEOUT_S` | 3 | 单次打开/读帧等待上限，0.1–15 秒 |
| `REALTIME__STOP_TIMEOUT_S` | 4 | 停止线程等待，1–15 秒；超时报 error 并禁止重复启动 |
| `REALTIME__PREVIEW_ENABLED` | true | 禁用画面不禁用内部消费者 |

非法值会在配置加载时明确报错，不静默修正。API 根据 `DATA_DIR/.camera-locks/usb-{index}.lock` 持有 OS 租约，防止共享该目录的进程重复打开同一设备；租约文件只有锁标志、不含画面。

## API / 权限

均使用原有 `Authorization: Bearer <token>`；不把 JWT 放 URL。

| 方法 / 路径 | 结果 |
|---|---|
| `GET /api/live-capture/status` | 状态及统计；禁用时仍可查询 |
| `POST /api/live-capture/start` | 管理员启动；同一会话重复调用不创建额外实例；未启用返回 503 |
| `POST /api/live-capture/stop` | 管理员幂等停止；超时返回 409 |
| `GET /api/live-capture/frame.jpg` | 当前 JPEG；无帧 204；预览禁用 403；模块禁用 503 |

未登录 401，跨机构/非管理员控制 403。首个启动者的机构持有整个会话；停止后其他机构管理员可发起新会话，不能读取旧机构画面。授权与状态/帧快照读取在同一锁中完成。预览 no-store，不持久化图像。

状态转换：`stopped → starting → running`；USB 故障转 `reconnecting`，有限退避后恢复或进入 `error / reconnect_exhausted`。正常文件 EOF 不视为断连。`last_error` 保留最近错误及时间，即使恢复后也可能非 null，应结合当前 state 和错误时间阅读。

## 帧契约与统计定义

- `VideoFrame`：camera_id / session_id / frame_id；同一 session 从 1 递增、重连不归零，新会话重置。BGR uint8 只读副本；width/height 是实际交付尺寸。
- `timestamp` 是帧到达父进程的 Unix 时钟，可能受系统校时影响；`monotonic_timestamp` 是本机单调时钟，用于顺序和帧龄。它们都不是位姿、世界坐标或原始曝光时间。
- `source_time_seconds` 仅可选文件解码 PTS；没有三维坐标、姿态信息。
- `nominal_fps` 是驱动/解码器报告，`requested_fps` 是配置；`input_fps` 按近 2 秒到达间隔实测，不足两帧为 0。
- `processed_fps` 按 `consume_latest()` 的实际出队间隔计算，不表示模型完成 FPS。没有消费者时为 null；预览不会增加消费计数。
- `frame_count` 是本会话成功交付数，`processed_frame_count` 是出队数；`queue_depth` 有界。
- `dropped_frames = overflow_dropped_frames + consumer_skipped_frames`，后者为消费者取最新帧时跳过的待处理帧。不包含设备内部丢帧、停止清空、未推理帧。
- `last_frame_timestamp` / `last_frame_age_ms` 为最后到达及其当前帧龄；`capture_latency_ms` 是最后一次 read（含 IPC）等待，不是曝光到屏幕延迟；`consumer_latency_ms` 是帧到达到出队的间隔。
- `end_to_end_latency_ms=null`：未接入真实推理/展示确认，不伪造 AI 延迟。时间粒度受平台时钟影响，短读帧可能报告 0 ms。

## 后续消费者接口

```python
manager = app.state.live_capture
frame = manager.consume_latest(timeout=0.2)  # 有限等待；关闭会唤醒等待者
if frame is not None:
    # frame.image 是 BGR uint8，模型转换颜色或修改前自行复制。
    # 用 session_id + frame_id 关联推理结果；单独记录推理完成时刻。
    pass
```

该队列是共享 work queue，**不是广播总线**。多个消费者直接调用会分摊帧；未来三种模型都需收到帧时，应由一个 dispatcher 读取后分发到各自有界队列，不能各自创建 VideoCapture 或在采集线程推理。模型结果帧龄、完成 FPS、端到端延迟应在下一阶段真实测量。

## 测试与限制

额外提供 [公开视频实际验证报告](realtime-public-video-validation.md) 和可离线执行的验收脚本：

```powershell
# 仓库根目录；媒体需自行合法下载，脚本不联网。
python backend/scripts/validate_live_capture.py --video .cache/realtime-test-media/pexels-10135156.mp4
python backend/scripts/validate_live_capture.py --synthetic
```

脚本使用原 FastAPI + Uvicorn 启动流程、真实鉴权 HTTP、原 CaptureManager/OpenCVSource；以独立临时数据库和空闲端口运行，不重启已有服务。`--synthetic` 明确标记为合成验证，普通单元测试不依赖下载或外网。详细参数见 `--help`。

```powershell
cd backend
python -m pytest tests/test_realtime_source.py tests/test_realtime_manager.py tests/test_live_capture_api.py -q
python -m pytest tests -q
cd ../app
flutter --no-version-check analyze --no-pub
flutter --no-version-check test --no-pub
```

真实本地小视频由测试生成，不上传视频或个人图像。无硬件时用可控源测试拔插/重连状态机，**尚未完成实体摄像头现场验收**，也未测实际 USB 持续运行 FPS。

P0 使用单 API 进程的内存状态，部署请 `--workers 1`、不要开启开发热重载进行持续采集。不同 DATA_DIR 或不同容器不共享设备租约；多进程集中采集、多相机、RTSP、跨模型广播不是本阶段已实现能力。

文件报告帧数未知时 OpenCV 无法可靠区分损坏尾部与正常 EOF；已知帧数过早失败报 `video_decode_failed`。驱动不返回、强制杀掉 API、系统崩溃后的硬件释放行为需现场验证，不能用正常关闭测试代替。当前不做实时 AI 告警、录制或音频采集。

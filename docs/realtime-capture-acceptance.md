# P0 实时视频基础设施验收记录

日期：2026-10-17。实施仓库：`anjing-vision-remote-latest`，分支 main；开发起点 `844ff7f9f818daf27a7447f57e7a60b440da2f2c`，当时 HEAD=origin/main、工作区干净。未改动正在运行的 renovation-sandbox 及其本机修改，也未重启既有服务。完整设计见 [审计](realtime-capture-audit.md)，运行方式见 [开发说明](realtime-capture.md)。

## 验收结论与边界

P0 的软件开发、可重复自动化验证及本地视频链路验证通过。**实体 USB 摄像头现场验收尚未完成**，不得视作硬件持续运行或真实拔插恢复已通过。

- USB / 文件共用 VideoSource 和 VideoFrame；输出实际 BGR、会话 ID、递增帧号、到达时间及尺寸。
- 可取消的子进程隔离驱动阻塞；唯一设备租约、有界线程安全队列、丢旧帧及完整生命周期。
- USB 有限退避重连；文件正常 EOF 不重连；停止唤醒等待者、释放采集线程和句柄。
- 复用 JWT / 机构 / 管理员权限，提供状态、幂等启停、JPEG；授权与快照读取原子化。
- Flutter 复用 Dio / Provider / Image.memory，项目页增加入口；无硬件、禁用、错误、后台切换和页面关闭均有正常降级。
- 统计区分输入、消费和预览，不报告虚构的推理 FPS 或 AI 端到端延迟。
- 默认关闭，不保存连续原始视频、不采音频、不加载新模型，不改变离线重建或风险算法。

## 真实测试结果

使用现有本机 Python 3.12.13、OpenCV 5.0.0 及现有 Flutter SDK，无依赖升级。

| 检查 | 修改前 | 修改后 | 新增失败 |
|---|---|---|---|
| 后端完整 `pytest tests -q` | 394 passed / 0 failed / 1 warning，89.16 秒 | 422 passed / 0 failed / 1 warning，94.87 秒 | 0 |
| Flutter analyze | No issues found | No issues found | 0 |
| Flutter 完整 test | 61 passed / 0 failed | 69 passed / 0 failed | 0 |
| P0 后端定向测试 | 无此模块 | 28 passed，9.19 秒 | 0 |
| P0 Flutter 定向测试 | 无此模块 | 8 passed | 0 |

另执行 `flutter --no-version-check build web --no-pub`，普通 Web 构建成功（60.8 秒）。Wasm dry-run 提示已有 `preview_launcher_web.dart` 使用 `dart:html`，不兼容 Wasm；本轮没有修改该文件，也未宣称 Wasm 支持。编译输出在被忽略的 `app/build/web`，不提交构建缓存。

两个后端全量报告均仅有原有 `test_scan46_delivery.py::test_bad_checksum_writes_no_data_or_database` 的 `zipfile.UserWarning: Duplicate name: data/work/46/postprocess/result.ply`。没有历史失败、新增 skip 或对断言的降级。完整后端覆盖原有抽帧、SLAM3R/SpatialLM 适配、结构/测量、空间指标、风险、PDF、权限及 Demo 等测试；这些是原有测试/模拟调用，不表示重新推理真实视频或模型。

运行命令（backend 目录）：

```powershell
python -m pytest tests/test_realtime_source.py tests/test_realtime_manager.py tests/test_live_capture_api.py -q -p no:cacheprovider
python -m pytest tests -q -p no:cacheprovider --junitxml=<隔离目录>/backend-final.xml
```

测试使用独立 `TEST_DATABASE_URL`，媒体目录沿用测试 conftest 的 `.test-data`；临时文件在本机 E 盘隔离目录，不触及正式数据。没有运行并发的后端 pytest。

Flutter（app 目录）：

```powershell
flutter --no-version-check analyze --no-pub
flutter --no-version-check test --no-pub test/live_capture_test.dart test/live_capture_page_test.dart --reporter expanded
flutter --no-version-check test --no-pub --reporter expanded
```

首次基线检查触发 Flutter SDK 的联网版本检查，GitHub fetch 连接重置；随后该次 analyze 与 61 项测试均正常完成。后续通过 `--no-version-check` 避免无关联网版本检查，未升级或重置 SDK。一次测试复制路径笔误导致新增文件未就位而未能加载，修正路径后完整重新执行，最终结果如表。

### 自动化覆盖

- 配置非法值、只读图像副本、真实生成的 MJPG 文件读取/尺寸/EOF、无效/缺失文件、设备租约互斥与释放、取消和子进程清理。
- 队列溢出、跳过旧帧的统计、有界生产消费、慢消费者、多等待者关闭、会话帧号/时钟、独立 JPEG、不伪造推理统计。
- 可控假 USB 源模拟打开失败、读帧失败、断连后恢复、已交付帧后继续编号、有限重试耗尽、停止重连、阻塞读取取消、重复启停、线程异常。
- 鉴权、管理员/成员权限、机构隔离、跨机构新会话防止旧请求读取新画面、默认禁用、无帧 204、预览开关、客户端断开、服务 lifespan 释放。
- Flutter 状态 null / 缺失值、Dio Bearer 与 bytes、启停、错误和离线展示、禁用与无画面、普通成员只读、预览关闭、后台/恢复、dispose 取消轮询。

USB 拔插部分使用假源，不冒充真实设备验证。

## 实际本地视频冒烟

在隔离 SQLite、临时管理员机构下，经 FastAPI TestClient + 真实 OpenCV spawn 子进程测试；没有启动或重启生产服务。输入为脚本生成的无隐私 MJPG 色块动画，不是真人摄像头。

| 指标 | 实测 |
|---|---|
| 输入 | 128 × 96，标称 12 FPS，目标 10 FPS |
| 总观察时间（含停止） | 8.188 秒 |
| 成功交付帧 | 78 |
| 最新 2 秒实际输入 FPS | 10.05 |
| 慢消费者实际出队 FPS | 1.75 |
| 消费帧数 | 14 |
| 队列容量 / 观测最大深度 | 2 / 2 |
| 溢出丢帧 / 出队跳帧 / 总计 | 48 / 14 / 62 |
| 成功鉴权 JPEG 响应 | 69，均真实解码为 128 × 96 |
| 采样末帧龄 | 93 ms |
| 最近 read + IPC 等待 | 0 ms，受本机单调时钟粒度影响 |
| 最近消费帧龄 | 47 ms |
| 停止耗时 | 79 ms |
| 重连 / 最近错误 | 0 / null |
| AI 端到端延迟 | null，未接入模型 |
| 停止后 | thread_alive=false，帧接口 204，采集子进程已释放 |

这些仅是小尺寸文件模拟的该次测量，不代表高清 USB、多人多端或实际 AI 推理性能承诺。冒烟脚本首次运行时发现编辑暂存副本遮蔽实际模块路径，已修正搜索路径为正式仓库；重复运行曾注册成已有机构普通成员（正确收到权限拒绝），改用临时新机构后重跑成功，未放松权限校验。

本机原始日志和测量 JSON 保留在工作目录 `_codex_stage/camera_p0/`，未上传：`backend-baseline.xml`、`backend-final.xml`、`flutter-baseline-*.log`、`flutter-final-test.log`、`smoke-result.json`。

## 修改清单

新增：

- `backend/realtime/__init__.py`、`config.py`、`frame.py`、`source.py`、`queue.py`、`manager.py`。
- `backend/app/routers/live_capture.py`。
- `backend/tests/test_realtime_source.py`、`test_realtime_manager.py`、`test_live_capture_api.py`。
- `app/lib/api/live_capture.dart`、`app/lib/pages/live_capture_page.dart`。
- `app/test/live_capture_test.dart`、`live_capture_page_test.dart`。
- `docs/realtime-capture-audit.md`、`realtime-capture.md`、本验收记录。

修改：`backend/app/config.py`、`backend/app/main.py`、`backend/.env.example`、`app/lib/api/client.dart`、`app/lib/pages/projects_page.dart`、`README.md`、`docs/INDEX.md`。

无删除文件，不更改数据库 schema、requirements、模型阈值、风险规则或 Scan46 正式产物。未提交 `.env`、密钥、录像、模型或测试缓存。

## 已验证的逻辑提交

| 提交 | 内容 | 提交前验证 |
|---|---|---|
| `cb31f6985f3cae3c979bc881e627e78c6d3ef4cb` | 输入协议与帧结构、审计 | source 7 passed |
| `485da1f32ec4fd8e98ac6a48a104d27e902ecf11` | 有界队列及生命周期 | source + manager 19 passed |
| `977177b190913caf4ea3cd5f6f41f202e570741c` | 鉴权控制、预览、Settings | 定向 API / 采集 / 配置 / auth 共 52 passed |
| `b2c8dc428888f1b88f3301cd77c6d1adbe2eaa8c` | 现有 Flutter 预览入口及测试 | analyze 无问题；定向 8、完整 69 passed |
| `025742cdf4a0b1ac4439f0d475c58ae56cca94aa` | 新旧会话切换时原子授权；重连后帧序验证 | 采集与 API 28 passed |

开发/验收文档作为另一个真实提交，在全量回归和文档检查通过后提交；其 SHA 与推送后 HEAD/origin/main 的核对结果随最终交付消息提供，避免在文档自身写入不可实现的自引用 commit SHA。推送仅允许正常 fast-forward，不覆盖远程历史。

## 未完成与后续

1. 实体摄像头权限、持续运行、真实拔插、被其他程序占用及异常关机后的恢复，需目标硬件现场验收。
2. P0 内存会话按单 API worker 部署；多进程集中采集、多路摄像头、RTSP、广播给多个模型尚未实现。
3. 下一阶段姿态模型应消费 `consume_latest(timeout)`，使用 session_id/frame_id 关联结果，在独立工作线程/进程完成推理并记录完成时间；不要新增 VideoCapture 或阻塞采集线程。三模型共用输入时增加独立有界 fan-out，而非多个模型争抢同一 work queue。
4. 功能默认关闭、现有运行服务不被替换。管理员需按开发说明配置并重启目标部署，然后在 Flutter 明确启动采集。

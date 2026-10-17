# P0 公开视频本地输入验证报告

日期：2026-10-17（Asia/Shanghai）。验证起点：main / `e0635e0c25c78f5979743ed6500ca6ed1a63770b`，工作区干净。本轮是新增的实际执行，不沿用上一轮 422 / 69 项测试推断公开视频通过。

## 1. 视频来源、许可和下载

- 视频：[Modern Minimalist Living Room](https://www.pexels.com/video/modern-minimalist-living-room-10135156/)，作者 Abdullah | 4K，内容为室内客厅与家具。
- 许可：[Pexels License](https://www.pexels.com/legal-pages/license/)。许可允许免费下载和使用；不得暗示背书、作冒犯性使用或作为素材平台再次分发等。本轮仅本地技术验证，不将原视频提交或再发布。
- 页面正常下载按钮指向：`https://videos.pexels.com/video-files/10135156/10135156-uhd_3840_2160_30fps.mp4`。
- 下载前 HEAD：200，`Content-Type: video/mp4`，`Content-Length: 33267054`；许可、来源及这些字段先写入本地 `source-before-download.json`，随后才下载。
- 下载后文件大小：33,267,054 bytes（约 31.73 MiB），与 HEAD 一致。
- SHA256：`3404e2d18933d310b451fe9f41bec755d566d728682aedab6f20d2debfd24d9e`。
- ffprobe 实测：MP4 / H.264，3840 × 2160，30 FPS，325 帧，10.833333 秒；本片仅视频轨道。
- 媒体位置：仓库根目录 `.cache/realtime-test-media/pexels-10135156.mp4`；来源记录也在该目录。

页面终端抓取曾遇到 Cloudflare 验证页，未尝试绕过；正常公开下载按钮的 CDN 链接无需登录，HEAD/下载均成功。一次 curl TLS 凭据错误后改用正常的 PowerShell HTTPS 客户端，无认证或访问控制规避。

先补充了精确忽略规则 `/.cache/realtime-test-media/`，`git check-ignore` 已验证媒体与来源 JSON 被忽略。下载成功后保留本地缓存供复测；无自动删除用户数据、无上传素材、未接触 Scan46 或正式报告。

## 2. 实际启动和输入链路

使用新增的 **验收驱动脚本** `backend/scripts/validate_live_capture.py`，不是替代生产采集实现：

```text
app.main.create_app(Settings)
→ Uvicorn Server + 正式 FastAPI lifespan（单 worker）
→ 真实 loopback HTTP 注册临时管理员
→ POST /api/live-capture/start
→ 原有 CaptureManager / OpenCVSource / VideoFrame / LatestFrameQueue
→ GET /api/live-capture/status、frame.jpg（Bearer 鉴权）
→ JPEG 解码验证 / consume_latest() 慢消费者
→ POST stop 或 Uvicorn 正常 shutdown
```

没有修改或另写 VideoCapture 替代实现。脚本通过 Uvicorn 的 Python 启动接口运行原应用，使用 OS 分配的空闲 loopback 端口，完整走真实 HTTP 而不是 TestClient。仅验收驱动器在独立线程运行 Uvicorn，以便从同一进程检查队列消费者和资源；生产应用、路由、驱动子进程及生命周期代码保持不变。

临时 SQLite、管理员及媒体输出都在每次唯一的 `backend/tmp/realtime-public-validation/<run-id>/` 下；随机生成测试凭据，不写入日志。不开实体 USB、不运行 Celery 重建任务、不重启现有 API/Demo、不加载 AI 模型、不改个人 `.env`。

配置：`source_type=file`，目标 10 FPS，640 × 480 输出上界，队列容量 2，沿用默认重试/超时。实际保持比例缩小为 **640 × 360**。文件按现有策略逐帧读取、不跳帧；325 帧约需 32.5 秒加启动/EOF 开销，不能把源视频的 30 FPS 或 10.83 秒时长当作本次模拟速度。

## 3. 逐项执行结果

以下数据来自首次交付报告的定稿运行 `20261017-120652-05426431`；首次运行 `20261017-120508-6daad09e` 的六组场景也通过，日志保留。本次提交前复核使用新的运行目录，见第 9 节，不覆盖这些历史实测。

| 验证项 | 实际观察 | 结果 |
|---|---|---|
| 打开及输出 | 真实 H.264 解码，完整交付 325/325 帧，与 ffprobe 帧数匹配 | 通过 |
| 帧契约 | 抽样 262 个不同 VideoFrame；ID 严格递增、session 不变、单调到达时间递增、Unix 时间有限且接近当前时间、BGR uint8 只读，实际尺寸与数组一致 | 通过 |
| 原有鉴权预览接口 | 264 次 JPEG 返回均能解码为 640 × 360；262 种画面；session/frame headers 与 no-store 正常；预览不消耗队列帧 | 通过 |
| 统计定义 | 名义/目标/实测 FPS 分离，消费者通过真实 consume_latest 出队，未伪造推理统计 | 通过 |
| 慢消费者与有界队列 | 最大深度 2，溢出丢旧帧 215、消费取最新时跳过 54、合计 269 | 通过 |
| 连续播放与 EOF | 连续运行 32.969 秒，stopped / end_of_file，等待清理后 thread_alive=false，reconnect_count=0，继续观察无新增帧/重连 | 通过 |
| 主动停止 | 另做三个未播完就停止的会话，清除快照及队列，预览返回 204，线程/采集子进程均退出 | 通过 |
| 重复启停 | 三个不同 session；每次重复 start 幂等、重复 stop 正常；每轮资源为空 | 通过 |
| 前端连接断开 | 真实关闭预览 HTTP 客户端 1 秒，采集继续增长且无新增采集实例；重连成功。之后正常 stop/shutdown 无遗留 | 通过 |
| 后端关闭 | 不先 POST stop，直接 Uvicorn 正常 shutdown，原 FastAPI lifespan 释放采集资源 | 通过（限正常关闭） |
| 缺失文件 | 明确 error / video_not_found；状态 HTTP 200；无重连、无遗留 | 通过（预期错误） |
| 损坏文件 | 本地伪 MP4 触发 error / source_open_failed，解码器报告 moov atom not found | 通过（预期错误） |
| 读取侧故障 | 读取已输出有效帧后，仅终止本轮自己的解码子进程；真实接口报告 capture_process_exited，文件不重连并释放线程 | 通过（故障注入） |
| Flutter 实际页面 | 本轮选择验证现有 JPEG 预览接口，没有重新进行 Flutter 浏览器/手机界面验收 | 未执行，不标为通过 |

正常 EOF 按既有设计保留最后至多两帧供消费者排空，不属于无限积压；主动 stop 后队列与快照清空。HTTP 客户端离开不应停止仍被消费者使用的共享采集，这与“无资源泄漏”并不矛盾。

## 4. 性能实测

| 指标 | 最终一轮实测 |
|---|---|
| 连续播放时间（到 EOF） | 32.969 秒 |
| 源 / 交付尺寸 | 3840 × 2160 / 640 × 360 |
| 源标称 / 配置目标 FPS | 30 / 10 |
| 基于观测帧 ID 与到达时间的投递 FPS | 9.982 |
| 暖机后滚动 input_fps 中位数 | 9.985 |
| 滚动消费者出队 FPS 中位数 | 1.68；刻意慢消费，含 HTTP/验证开销，不是 AI FPS |
| 交付 / 已消费 / EOF 待消费 | 325 / 54 / 2 |
| 丢帧 | 269 = 溢出 215 + 消费跳帧 54；325 = 54 + 269 + 2 |
| 队列容量 / 最大深度 | 2 / 2 |
| 最后一次 read+IPC 等待 | 约 46 ms；不是曝光到屏幕延迟 |
| 最后消费者队列等待 | 约 15 ms；不是推理耗时 |
| 三次主动停止耗时 | 59.722 / 50.578 / 53.599 ms |
| EOF 后幂等停止耗时 | 7.767 ms |
| 正常播放 last_error / reconnect_count | null / 0 |
| 全部结束后的采集线程 / 子进程 | 0 / 0 |
| 端到端延迟 P50 / P95 | **不可计算 / null** |

缺少可靠的曝光时间、模型完成时刻和前端实际呈现确认，文件 PTS 也不是本机原始拍摄 Unix 时间，因此不将 read 耗时、队列帧龄或 HTTP 返回时间冒充端到端延迟。

这仅是一段 10.83 秒源片的短时本地输入验证，不是小时级稳定性、实体摄像头或真实 AI 性能承诺。没有预设通过必须达到某个 FPS。

## 5. 可复现命令与自动化

从仓库根目录，使用现有后端 Python 环境：

```powershell
# 先自行按上述许可正常下载视频；脚本不会联网下载，也不暗中换视频。
python backend/scripts/validate_live_capture.py `
  --video .cache/realtime-test-media/pexels-10135156.mp4 --deadline 65

# 无网络 / 无公开视频时，明确使用合成 MJPG；不能称为公开视频验收。
python backend/scripts/validate_live_capture.py --synthetic --deadline 30

cd backend
python -m pytest tests/test_realtime_source.py tests/test_realtime_manager.py `
  tests/test_live_capture_api.py tests/test_live_capture_validation.py -q
python -m pytest tests -q
```

`--video` 和 `--synthetic` 必须且只能选一个；路径、fps、deadline 做校验。默认输出为 backend/tmp 下每次唯一的目录，不覆盖旧文件。可用 `--output-root` 指定临时目录。脚本不依赖 ffprobe 才能运行；存在 ffprobe 时额外记录原编码/帧数并核对完整度。

新增 `test_live_capture_validation.py`：在子进程用生成的 30 帧 MJPG 执行相同六组场景，另外三项覆盖命令参数错误。普通 pytest 不下载视频、不访问公网、不依赖实体摄像头。

实际 P0 定向回归：**32 passed，20.76 秒**（28 个既有 P0 测试 + 4 个新增测试），不是沿用上轮结果。最终脚本版本的完整后端回归：**426 passed，0 failed，1 warning，89.15 秒**。警告仅为原有 `test_scan46_delivery.py::test_bad_checksum_writes_no_data_or_database` 的 ZIP 重复条目，新增失败为 0。原始结果在本机 `backend-full.xml`；Flutter 未改，本轮不把其旧测试结果当作新的界面验收。

## 6. 日志、失败与修复

首次交付定稿的公开视频运行目录：`backend/tmp/realtime-public-validation/20261017-120652-05426431/`：

- `result.json`：六组场景、性能和资源检查结果。
- `frames.jsonl`：观测帧 ID、两个到达时间戳、实际尺寸。
- `status-samples.json`：逐次状态和队列/FPS 样本。
- `runtime.log`：正式 API、采集状态与 Uvicorn 生命周期日志；不包含 token 或像素内容。
- `validation.db`、损坏文件 fixture：仅本机隔离验收数据。

公开视频日志只有故意注入的三个 capture WARNING（缺失、损坏、解码进程退出），没有非预期 ERROR 或 Traceback。注意 `uvicorn.error` 是 logger 名称，其 INFO 启停行不是 ERROR 级别。

调试时修正了验收脚本的两个问题：重连后的 HTTP client 纳入统一 finally 清理；EOF 后等待采集 finally 完成再记录最终状态，避免报告中出现短暂的 stopped/thread_alive=true。并加入与 ffprobe 总帧数核对。**没有因此修改生产 P0 源/管理器/API。** 一次测试复制路径笔误导致未找到新增测试、未运行；修正目录后重新执行，不能将该次当作通过。

后端回归日志保留在本机 `E:/anlingzhijing/anjing-vision/_codex_stage/camera_public/`：`targeted.log`、`backend-full.log`、`backend-full.xml`。测试媒体、运行日志、临时数据库未加入 Git；报告保留来源、哈希、指标和复现步骤。

## 7. 本轮代码和 Git 状态

本轮新增/修改：

1. `.gitignore`：只忽略专用测试媒体目录。
2. `backend/scripts/validate_live_capture.py`：可重复的真实应用/HTTP 验收驱动器。
3. `backend/tests/test_live_capture_validation.py`：不依赖外网的脚本回归。
4. `docs/realtime-public-video-validation.md`：本报告。
5. `docs/realtime-capture.md`：增加验证脚本入口。

未修改 `backend/realtime/`、生产路由、前端、模型、阈值、数据库结构或任何 Scan46 数据。没有下载新模型、没有运行姿态/跌倒/环境风险/材质识别。

首次验证交付时，上述五项为未提交修改，HEAD 为 `e0635e0c25c78f5979743ed6500ca6ed1a63770b`。本次经用户授权，按五文件单个功能提交安全整理并同步 main；最终发布提交 SHA 及 HEAD/origin/main/GitHub 核对结果随交付消息提供，避免文档自引用其自身 commit SHA。媒体继续被忽略、不进入提交。

## 8. 仍待验收

- 实体 USB 摄像头的 OS 权限、真实尺寸/FPS、长时间稳定性、真实拔插恢复、其他软件占用设备。
- 目标部署的 Flutter 页面实际显示、移动端后台切换现场检查。
- 强制杀掉 API、断电或系统崩溃后的硬件资源恢复；本轮只测试 Uvicorn 正常 shutdown。
- 真实 AI 消费者、推理完成 FPS 和曝光到展示的端到端延迟。

本报告只证明已执行的本地视频输入链路，不证明实体 USB 摄像头拔插验收完成。

## 9. 提交前安全审查与重新执行（2026-10-17）

- 分支 main，起点 HEAD/origin/main 均为 `e0635e0c25c78f5979743ed6500ca6ed1a63770b`；fetch 后远程无新增提交，初始暂存区为空。逐项审查 tracked diff 与三个未跟踪文件，只有第 7 节五项相关成果。
- 本地 Pexels 文件哈希未改变，仍在精确忽略目录，`git ls-files -- .cache/realtime-test-media` 无输出。新增忽略规则没有扩大到其他业务目录。
- 五文件均小于 20 KB，未发现私钥或常见 token 格式；测试凭据使用运行时随机值。没有 `.env`、密钥、权重、视频、数据库、缓存或无关用户文件进入提交范围。
- 脚本没有下载逻辑；只有 loopback HTTP 调用生产 API。普通离线测试使用临时生成的 MJPG，覆盖同一六组场景，不依赖公网。
- 本次重新执行 P0 定向测试：**32 passed，16.88 秒**。本次重新执行完整后端：**426 passed，0 failed，1 个原有 ZIP 警告，62.07 秒**，新增失败为 0。Flutter 和 P0 核心未改，未冒充重新完成 UI 或 USB 现场验收。
- 新公开视频复现目录：`backend/tmp/realtime-public-validation/20261017-121445-c6a453ee/`，六组检查全部通过。
- 此次连续播放 32.984 秒，325/325 帧，输出 640×360；帧序/到达时间实测投递 10.022 FPS，滚动输入中位数 10.0 FPS，慢消费者中位数 1.83 FPS。
- JPEG 解码 287 次，285 种不同预览；队列最大 2，消费 58、丢帧 265、EOF 待消费 2，满足 `325=58+265+2`。
- 三次主动停止 57.890 / 60.130 / 56.267 ms；文件结束不重连；前端 HTTP 断开/重连和 Uvicorn 正常 shutdown 后资源检查通过。所有场景结束时采集线程/子进程均为 0。
- 此次日志无非预期 ERROR/Traceback，仅三个故意注入的 capture WARNING。端到端 P50/P95 仍不可计算，未补造数据。
- 本次定向/全量原始日志与 XML：本机 `E:/anlingzhijing/anjing-vision/_codex_stage/p0_publish/targeted.log`、`full.log`、`full.xml`；不纳入仓库。

复现命令与第 5 节一致；本次实际解释器为 `E:/anlingzhijing/anjing-vision/.venv/python.exe`。没有为了提交增加空提交、重复提交或修改原有断言。

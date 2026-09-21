# R2 冻结地图重定位实施计划

## 2026-09-15 用户批准的路线替换

以下旧 render 路线停止。新实验为原扫描真实 RGB → 像素对齐 registered point map → 既有坐标变换 → 多旧帧身份一致性 → 空间留出相机验证。参考 hloc/localize_sfm.py 的真实观测身份组织方式，不采用其失败回退位姿。

- [x] 找到 scan45 全900帧、原始点图与 confidence，并核对真实预处理源码。
- [x] 为多帧身份与深度断层拒绝规则先写失败测试，再实现。
- [x] 全序列检索、原始高分辨率图像匹配、原像素直接查3D。
- [x] 检查旧帧刚体掩码、多帧3D身份；空间独立留出验证相机（未通过）。
- [x] 相机未通过，完成原始观测可定位性审计；R2恢复未完成。
- [x] 2026-09-19校验正式七文件哈希全部一致；28项测试通过。见 raw_visual_map/原始视觉地图验证.md。

**Goal:** 先证明全幅补拍相机在冻结 baseline 内的位置，再生成 R2 候选。

**Architecture:** baseline RGB/depth/point-ID 渲染 → 刚体图像匹配 → 点身份可追溯的 PnP → 空间留出验证。旧相机仅作为参考图检索初始视角，旧 point map 不提供定位对应。

**Tech Stack:** 现有 NumPy/OpenCV/SciPy/Open3D/Pillow，先用 SIFT，不改变正式应用或安装新模型。

**Spec:** 扫描46 R2 地图重定位与独立刚体验证要求。

## 约束

- 正式 baseline、visual、展示选择、尺寸、评分、结构、通行数据全部冻结。
- 全幅 RGB 做定位；窗帘、靠垫、袋子、床品不进入相机锚点。
- 不把最近邻当对应，不用 ICP 或不可靠相机补 R2。
- 在当前会话执行，不要求用户选择执行模式，不自动提交或上传。

## 执行步骤

- [ ] 为 `backend/scripts/local_gap_geometry.py` 的 `render_identity(points, colors, K, R, C, shape, radius)` 写测试：同像素近点遮挡远点、空像素 ID=-1、相机后方点不能渲染、颜色和 ID 一致；先失败后实现。
- [ ] 新建单一定位实验入口 `backend/scripts/localize_r2_map.py`，输出到既有 target_r2_20260914/map_localization；抽取全幅 21 帧和 5 个关键帧 contact sheet。输出基线视角及 RGB/depth/ID，人工查看刚体可见性。
- [ ] SIFT 匹配必须同时在刚体掩码内，参考特征读取真实点 ID，不插值跨表面 XYZ；保存双图匹配、源像素、点 ID、区域、使用/留出身份。
- [ ] PnP 只拟合训练刚体；完整空间区域留出。对失败关键帧进入明确角点核验，而不是放宽阈值或改回近邻。
- [ ] 至少独立刚体三角化和多帧几何通过，才连接 21 帧轨迹并进入 R2。没有足够身份明确点时记录具体可见性与地图不足，不宣称相机已成功。
- [ ] 运行几何回归测试，比较 7 个 baseline SHA-256；提交文字结论与实际证据，不把诊断表面当作合格候选。

参考实现：https://github.com/cvg/Hierarchical-Localization/blob/master/hloc/localize_inloc.py

## 2026-09-15 实际执行状态

- 完成带 point ID 的遮挡正确渲染及测试；完成 21 张全幅图、35 个地图视角。
- 完成刚体匹配、地图表面取值校正、空间留出 PnP 试验及 8 个人工候选核验。
- **未完成**可靠相机和 R2：当前未取得足够身份明确、空间独立的对应点；没有在失败位姿上继续深度优化。
- 完成冻结文件校验；详细证据位于 `backend/data/work/46/local_gaps_20260912/target_r2_20260914/map_localization/地图重定位验证.md`。

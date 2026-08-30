# 风险评估与评分模块开发报告

## 1. 开发目标

本轮目标是在不重新执行上游识别与重建的前提下，基于已有结构图、通行图及其结构化派生数据，建立统一的适老空间风险评估链路。已完成：

- 15 项正式空间指标（SpatialMetric）；
- 入口到床等结构化路径与通行评估；
- 家具间距、墙体净空、床侧净空和拥挤度等布局指标；
- 后端单一来源、带版本的风险规则；
- 通行能力 40%、空间布局 30%、使用安全 30% 的正式评分；
- 与风险等级分离的 evidence confidence 和 assessment coverage；
- Top Risks 排序和具体整改建议；
- 统一 `risk_assessment.json`；
- 只使用结构化坐标的 risk figure；
- 直接消费正式评估结果的 PDF；
- Pipeline、API 和 Flutter 报告页集成。

## 2. 数据边界

正式风险链只读取已经生成的结构化 JSON。验收没有重新执行：

- SLAM3R；
- SpatialLM；
- GroundingDINO；
- SAM；
- 点云分析；
- 家具重新识别；
- 实例聚类或关联；
- 家具长、宽、高重新计算。

本模块遵守 **UNKNOWN != SAFE**。缺少可靠输入时，指标和风险必须保留为 `not_evaluable`，不得按安全项计分，也不得用房间中心、固定坐标或类别先验伪造证据。

## 3. 开发基线

- 开发基准 SHA：`726e3320f38b6272773a14344ccdc8b252375cdf`
- 最终功能 SHA：`7ce10fa85dde29b0e442fa474ff56b7499ddbefa`
- 最终功能 SHA 已与 `origin/main` 对齐。

## 4. Scan46 结构化输入审计

验收输入目录：

`E:\anlingzhijing\anjing-vision\scan46_完整数据包\data\work\46\postprocess`

实际使用：

1. `measurements.json`：已经通过测量门控的房间、门和家具尺寸；
2. `passage_analysis.json`：已有 2D 结构推导的通路、瓶颈和净距；
3. `spatial_foundation.json`：规范化房间、门、家具和路径结构；
4. `structure_calibrated.json`：risk figure 使用的米制结构坐标。

验收前后分别计算四个输入文件的 SHA256，结果完全一致。视频、图像、PLY、相机、mask 和模型中间输出均未进入本次计算。

## 5. SpatialMetric

正式 SpatialMetric 字段为：

- `category`
- `metric_code`
- `name`
- `value`
- `unit`
- `status`
- `confidence`
- `position`
- `reason`
- `source`

当前 15 个正式 `metric_code`：

1. `main_passage_width`
2. `minimum_passage_width`
3. `door_width`
4. `entrance_space`
5. `path_length`
6. `path_continuity`
7. `path_obstruction`
8. `furniture_spacing`
9. `wall_furniture_clearance`
10. `bed_wall_distance`
11. `bedside_clearance`
12. `activity_area`
13. `crowding`
14. `bed_surrounding_space`
15. `main_activity_area_safety`

`measured`/`derived` 表示存在可追溯证据并可进入评估；`not_evaluable` 表示证据不足，必须附带 `reason`，且不能转成安全结论。

## 6. Spatial Paths

路径层将已有通路结构规范化为统一路径记录，包含 `path_id`、起点、目标、路径长度、连续性、是否受阻、瓶颈宽度、瓶颈坐标、置信度及状态。

Scan46 的正式路径为 `door_to_bed`：从 `door_01` 到 `bed_001`，长度 1.36m，连续、未受阻，最小净宽 0.48m，瓶颈位置 `[1.8156, 0.639]`。

系统没有凭空创建入口到活动区的路径。由于没有显式 activity anchor，`entrance_to_activity` 保持 `not_evaluable`。

## 7. Layout Metrics

布局指标只消费结构化家具和通路关系，包含：

- 最近家具间距；
- 家具与房间边界净空；
- 床离墙距离；
- 床侧最近家具净空；
- 显式活动区面积；
- 家具 footprint 占房间面积的 crowding 比例；
- 床周边最小净空；
- 主活动区安全证据。

缺少显式活动区对象时不会把房间中心当成活动区。

## 8. RiskResult

正式 RiskResult 字段为：

- `risk_code`
- `risk_type`
- `risk_name`
- `metric_code`
- `measured_value`
- `unit`
- `threshold`
- `position`
- `risk_level`
- `confidence`
- `reason`
- `advice`
- `assessment_status`
- `related_object_ids`
- `related_path_id`

Scan46 实际生成的 `risk_code`：

- `main_passage_width_high`
- `minimum_passage_width_high`
- `door_width_medium`
- `entrance_space_low`
- `path_length_low`
- `path_continuity_low`
- `path_obstruction_low`
- `furniture_spacing_high`
- `wall_furniture_clearance_medium`
- `bed_wall_distance_medium`
- `bedside_clearance_high`
- `activity_area_not_evaluable`
- `crowding_medium`
- `bed_surrounding_space_high`
- `main_activity_area_safety_not_evaluable`

`not_evaluable` 项的 `risk_level` 必须为 `null`，不能写成 `low`。

## 9. Versioned Rules

正式规则集中在 `backend/pipeline/rules.py`，每条规则包含稳定的 rule code、metric code、方向、阈值、严重程度、参考说明、版本和建议模板。

风险判断只在后端执行。Flutter、PDF 和 risk figure 均直接消费后端结果，不复制阈值、不重新判断风险等级。

## 10. 正式评分

类别权重固定为：

- Mobility / Passage：40%
- Space Layout：30%
- Usage Safety：30%

类别分数来自该类别内已经评估的 RiskResult。只有覆盖率、核心指标和类别完整性满足正式门控时才生成 overall score；否则 overall 状态为 `insufficient_data`，不会因为未知项产生虚假高分。

## 11. Confidence 和 Coverage

以下三个概念相互独立：

- `risk_level`：指标相对规则阈值的风险严重程度；
- `evidence_confidence`：已评估证据本身的平均置信度；
- `assessment_coverage`：可评估项占全部正式指标的比例。

系统同时提供 `coverage_adjusted_confidence`，用于表达证据置信度经过覆盖率折减后的整体可信程度。高置信度不等于低风险，高覆盖率也不等于安全。

## 12. Top Risks 和 Advice

Top Risks 仅从已评估的高、中风险项中选取。排序首先按高风险优先于中风险，再按可用置信度降序，最后用稳定的 `risk_code` 保证结果可复现。

整改建议来自后端版本化规则中的建议模板，按 Top Risks 顺序去重。展示层不重新生成建议。

## 13. Unified `risk_assessment.json`

统一正式结果的主要结构为：

- `schema_version`
- `official`
- `overall`
- `category_scores`
- `weights`
- `key_metrics`
- `metrics`
- `paths`
- `risks`
- `top_risks`
- `not_evaluable`
- `advice`
- `confidence`
- `provenance`
- `scope`
- `metric_input`

真实 artifact 追踪通过 `provenance.inputs` 和 `metric_input` 记录输入文件名、SHA256 和 `input_modified: false`。正式 schema 没有额外的 overall level 字段，本轮没有为了报告新增该字段。

## 14. Risk Figure

`formal_risk_figure.py` 只读取 `risk_assessment.json` 和已经接受的结构 JSON。它将正式风险中的 `point_xy`、`center_xyz` 或已有 object ID 解析到房间平面图，不能定位的风险不会获得伪造坐标。

该图没有重新读取或分析点云，也不重新计算风险。它是结构化正式风险的 2.5D 位置展示，不是新的三维识别模块。

## 15. PDF

PDF 直接消费正式 `risk_assessment`，显示 overall score、40/30/30 类别分数、coverage、confidence、关键指标、证据不足项、风险和建议，并可嵌入 risk figure。

PDF 不执行阈值判断、不重算分数，并明确展示 `not_evaluable` 原因。

## 16. Flutter

Flutter 报告页已支持：

- 正式 overall score；
- 三类评分及权重；
- coverage；
- confidence；
- Top Risks；
- `not_evaluable` 及原因；
- 后端关键指标；
- risk figure；
- 正式 PDF 打开入口。

模型兼容 risk figure URL、PDF URL、`null`、空字符串、字段缺失和旧报告格式。PDF URL 为空时不显示无效按钮。

## 17. API / Pipeline

Pipeline 在已有结构化测量完成后生成：

- `spatial_metrics.json`
- `risk_assessment.json`
- `formal_risks.png`
- `report.pdf`

API 提供当前正式评估、空间指标、报告图片和 PDF。报告接口优先读取当前 `risk_assessment.json`，避免数据库中的旧快照覆盖正式结果。Flutter 通过报告 JSON 中的 `images` 和 `preview` artifact links 使用这些产物。

## 18. Scan46 最终验收

### 18.1 指标

Scan46 共生成 15 项正式指标，其中 13 项 evaluated：

| Metric | Status | Value |
| --- | --- | ---: |
| `main_passage_width` | derived | 0.480m |
| `minimum_passage_width` | derived | 0.480m |
| `door_width` | measured | 0.846m |
| `entrance_space` | derived | 2.163m² |
| `path_length` | derived | 1.360m |
| `path_continuity` | derived | true |
| `path_obstruction` | derived | false |
| `furniture_spacing` | derived | 0.040m |
| `wall_furniture_clearance` | derived | 0.000m |
| `bed_wall_distance` | derived | 0.000m |
| `bedside_clearance` | derived | 0.300m |
| `crowding` | derived | 0.5315 |
| `bed_surrounding_space` | derived | 0.000m |

两项 `not_evaluable`：

| Metric | Reason |
| --- | --- |
| `activity_area` | `explicit_activity_anchor_missing` |
| `main_activity_area_safety` | `explicit_activity_anchor_missing` |

两项均未被标记为 safe 或 low risk。

### 18.2 评分与可信度

- Mobility / Passage：71.4
- Space Layout：44.0
- Usage Safety：20.0
- Overall：47.8
- Overall status：`evaluated`
- Raw evidence confidence：0.850
- Coverage-adjusted confidence：0.737
- Assessment coverage：86.7%（13/15）

### 18.3 Top Risks

1. 床周边最小净空：高风险，0.00m；
2. 床侧净空：高风险，0.30m；
3. 家具间距：高风险，0.04m；
4. 主要通道净宽：高风险，0.48m；
5. 最小通道净宽：高风险，0.48m；
6. 床离墙距离：中风险，0.00m。

整改建议已按上述风险正常生成。

### 18.4 验收产物

- `risk_assessment.json`：成功，35,171 bytes；
- `formal_risks.png`：成功，41,086 bytes；
- `report.pdf`：成功，46,354 bytes；
- Flutter artifact parsing：成功。

独立验收产物位于：

`E:\anlingzhijing\anjing-vision\.recovery\scan46-final-acceptance-7ce10fa`

## 19. 最终测试结果

### Backend

- 314 passed
- 4 historical failures
- 0 new failures
- 0 skipped
- 0 warnings
- 耗时 62.10s

历史失败：

1. `tests/test_measurement_builder.py::test_references_compute_one_global_scale`
2. `tests/test_measurement_builder.py::test_validation_reference_is_not_used_to_compute_scale`
3. `tests/test_semantic_evidence.py::test_keyframe_selection_is_uniform_bounded_and_never_duplicates_low_fps_input`
4. `tests/test_structure_builder.py::test_unlabelled_floor_obstacle_is_kept_for_risk_analysis`

### Flutter

- `flutter analyze`：`No issues found`
- `flutter test`：55 passed，6 historical failures
- 本轮模型与 artifact links 测试：8/8 passed
- 正式 PDF 入口组件测试：1/1 passed

## 20. 历史遗留问题

Backend 上述 4 项和 Flutter 下列 6 项均归类为 **existing baseline issues**，本任务没有通过降低断言、删除测试、skip 或无关业务改动掩盖它们，也不声称已经解决：

1. `显示安全评分、风险项和改造建议`
2. `无风险和无建议时显示明确空状态`
3. `未知风险状态使用帮助图标而不是正常图标`
4. `标定成功时显示参考尺寸和重建空间尺寸`
5. `标定失败时仍显示输入值和失败原因`
6. `第二阶段语义空间：显示实例卡片与门洞宽高`

这些 Flutter 失败集中于既有报告页测试的旧文案或滚动可见范围断言。本轮新增功能的定向测试均通过。

## 21. 本轮 Git 统计

以下统计由 Git 对功能范围 `726e3320f38b6272773a14344ccdc8b252375cdf..7ce10fa85dde29b0e442fa474ff56b7499ddbefa` 实际计算：

- 真实功能提交：50
- 变更文件：33
- 新增文件：17
- 修改文件：16
- 删除文件：0
- Insertions：3,439
- Deletions：71
- Net additions：3,368

正式报告本身将在上述 50 个功能提交之后单独提交，因此最终分支提交数将增加到 51；这不会改变上述“功能范围”统计。

### 21.1 50 个功能提交

| # | SHA | Commit message |
| ---: | --- | --- |
| 1 | `5c52123` | feat: retain semantic detection support diagnostics |
| 2 | `790f9e5` | docs: record risk assessment baseline |
| 3 | `a54141c` | docs: audit scan46 structured risk inputs |
| 4 | `250503a` | feat(metrics): define formal spatial metric contract |
| 5 | `8ddf9a2` | feat(metrics): catalog formal assessment metrics |
| 6 | `9cf3f14` | feat(metrics): centralize formal metric construction |
| 7 | `7ee5ced` | feat(metrics): normalize structured confidence values |
| 8 | `1ff039c` | feat(metrics): validate complete metric payloads |
| 9 | `34d5995` | feat(metrics): derive structured passage widths |
| 10 | `4ea5643` | feat(metrics): extract verified entrance door width |
| 11 | `e0da925` | feat(metrics): expose entrance walkable space |
| 12 | `fc0ac3b` | feat(paths): normalize structured assessment paths |
| 13 | `ea0e8e3` | feat(paths): derive formal path metrics |
| 14 | `24401d8` | feat(layout): derive furniture spacing metric |
| 15 | `528a175` | feat(layout): derive furniture wall clearances |
| 16 | `b1d0558` | feat(layout): derive bedside furniture clearance |
| 17 | `34fcfcf` | feat(layout): derive activity area and crowding |
| 18 | `afd0f41` | fix(metrics): define bed surrounding space as clearance |
| 19 | `b55b15b` | feat(layout): derive bed surrounding clearance |
| 20 | `8cd7740` | feat(layout): derive activity area safety evidence |
| 21 | `25bb5c6` | feat(metrics): assemble complete structured inputs |
| 22 | `2c86516` | feat(metrics): persist JSON-only assessment inputs |
| 23 | `f6d6047` | docs: accept scan46 formal spatial metrics |
| 24 | `849b503` | feat(risk): define formal risk result contract |
| 25 | `31c1e17` | feat(rules): centralize versioned formal risk rules |
| 26 | `5cbc4f9` | feat(risk): evaluate formal metrics with versioned rules |
| 27 | `286bbcb` | feat(scoring): define official 40 30 30 weights |
| 28 | `cfcc10b` | feat(scoring): compute official weighted assessment |
| 29 | `34715b5` | feat(risk): report confidence and assessment coverage |
| 30 | `5f15b45` | feat(risk): rank top risks and specific advice |
| 31 | `56233f7` | feat(risk): compose unified assessment payload |
| 32 | `862768f` | feat(risk): persist unified assessment JSON |
| 33 | `3f77969` | feat(risk): build formal assessment artifact chain |
| 34 | `57e4f1f` | feat(pipeline): generate formal risk assessment |
| 35 | `eda418b` | feat(api): serve formal assessment artifacts |
| 36 | `5ddcc56` | feat(api): prefer current formal risk assessment |
| 37 | `961cf22` | feat(flutter): parse formal risk assessment fields |
| 38 | `701fcc8` | feat(flutter): render formal risk cards |
| 39 | `54dc11e` | feat(flutter): present official assessment summary |
| 40 | `6b783f8` | feat(flutter): render backend key metrics |
| 41 | `3b3d361` | feat(report): use formal assessment in PDF |
| 42 | `350dbd7` | feat(report): show formal category confidence |
| 43 | `2f8aff2` | feat(report): show key metrics and evidence gaps |
| 44 | `71dc3d7` | feat(report): compose from formal assessment |
| 45 | `e9175be` | feat(pipeline): generate official PDF report |
| 46 | `9a90cc2` | feat(report): render structured formal risk map |
| 47 | `2468894` | feat(pipeline): publish formal risk report artifacts |
| 48 | `731982b` | feat(flutter): parse formal report artifact links |
| 49 | `77fc839` | feat(ui): add risk assessment PDF action |
| 50 | `7ce10fa` | test(ui): cover risk report artifact links |

## 22. 完成情况

本任务开发目标已完成：

- `NEW FAILURES = 0`；
- 50 个真实功能提交已推送到 `main`；
- 正式报告作为第 51 个提交单独保存；
- Scan46 正式结构化链路、风险 JSON、risk figure、PDF 和 Flutter artifact parsing 均验收通过；
- 未为 Scan46 增加专属 ID、坐标、阈值或判断；
- 未修改或提交 `backend/.risk-render-test/`。

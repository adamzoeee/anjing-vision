# 第二轮风险评估质量审计与首批修复

审计日期：2026-09-07。审计目标为结构化风险链路，未运行重建、识别、点云分析或家具尺寸生成。

## 1. 版本与证据范围

- 用户指定仓库：`E:\anlingzhijing\anjing-vision-remote-latest`。
- 开始时本地 HEAD：`5bf11e27a8775b1a56410efe75b6a0c5c0e22cec`。
- fetch 后远程 main：`6795677f2648020e75a79854caba0a7621711062`，新增 5 个提交，包含报告页及改造助手更新。
- 审计以最新 main 为准；首批修复从该提交建立独立工作区，未覆盖原项目的测试产物。
- 读取了 spatial_metrics、spatial_paths、spatial_layout、risk_assessment、rules、formal_risk_figure、report_builder、report_composer、pdf_report、API、Flutter models/widgets/tests，以及调用关系涉及的 space_foundation 和改造助手。
- 首批之外的结论是代码审计，不代表已修复。旧版全量测试统计不能当作此版本的测试结果。

用户补充确认：完成审计后允许修复、验证首批 P0，再合并 main。后续工作均按独立、可验证批次进行。

## 2. 当前链路及已确认的正确行为

`measurements + structure → space_foundation / passage_analysis → SpatialMetric + normalized paths → RiskResult → 40/30/30评分 → confidence/coverage → Top Risks/advice → JSON/风险图/PDF → API/Flutter`。

正式 pipeline 的评估失败不会调用旧 40/40/20 评分兜底：报告保留 `score=null`，并记录 `generation_failed`。PDF通过 `compose_report(points=None, risk_assessment=...)` 消费同一份正式评估，不分析点云。Flutter正式风险卡使用后端 `top_risks`，不重新应用正式阈值。

正式指标目录有15项。`build_metric_payload` 已检查缺失/重复/未知代码；现有标量检查不完整，直接读入JSON的评估入口还能绕过目录检查，见 Q01/Q02。

正式门宽要求 `measurement_status=verified`；活动区主链要求明确的 `activity_area/activity_anchor`，不能从房间中心猜测。`detour` 当前只透传；没有被列入15项正式评分，不应谎称已实现独立绕行风险。门口障碍目前依据路径关系，没有独立门扇开启空间证据。

### 评分、置信度和覆盖率的实际公式

- 每项已评估风险映射分数：low=100、medium=60、high=20。
- 类别分数 = 本类别已评估项的上述分数均值，保留1位小数；该类别完全无数据则分数为null。
- 总分 = 通行能力×0.40 + 布局×0.30 + 使用安全×0.30，保留1位小数。
- 总分要求：评估覆盖率至少60%、5项核心指标全部可评估、3个类别均有可评估项；否则 `insufficient_data/score=null`。
- 风险覆盖率 = evaluated风险数 / 全部正式风险数。15项等计数，不是风险权重。
- 证据置信度 = 已评估风险中非null confidence的均值，保留3位小数。
- 覆盖修正置信度 = 证据置信度 × evaluated数 / 总数，保留3位小数。`overall.confidence`引用该值，没有在正式评分链中再次乘覆盖率。
- 缺少非核心项仍可产生较高条件性分数：未知项不算安全，但也不扣分；必须同时展示覆盖率。覆盖权重和分数政策本轮不调整。

### Scan46现有产物检查

只读：`E:\anlingzhijing\anjing-vision\.recovery\scan46-final-acceptance-7ce10fa\risk_assessment.json`及`spatial_metrics.json`。

- 总分47.8，状态evaluated，13/15覆盖，86.7%。
- `activity_area`与`main_activity_area_safety`均缺少显式活动锚点。
- 证据置信度0.850，覆盖修正0.737；非null置信度样本只有4项，不能解释为13项都有85%置信度。
- `main_passage_width`与`minimum_passage_width`均为0.48m、同一路径、同一瓶颈坐标，Top Risks确有重复展示。
- `bed_surrounding_space=0`的证据标记为床与墙边界；不能把这个0一概当成缺失。
- 本次只读取保存的验收产物，不把旧47.8分视为真实房间安全真值。

## 3. 问题清单

以下位置均对应审计基线6795677，行号以函数位置为参考。每项列出行为、原因、方案、schema/Scan46影响、测试、等级和推荐提交。

### Q01 — P0：无效数值或布尔证据可能生成低风险（首批修复）

- 位置：`spatial_metrics.py:41,53,112`；`risk_assessment.py:67,100`。
- 当前行为：已实际复现NaN门宽得到`door_width_low`；字符串`"false"`的路径连续性得到low；`confidence_value(NaN)=1.0`。
- 原因：缺少有限数、真正布尔类型、数值单位及状态校验；NaN比较恒不触发阈值，字符串不等于布尔False。
- 修改：在公共正式指标入口和JSON评估入口共用规范化；无效证据变为not_evaluable/null并记录原因；保留合法0/False。无效置信度不得变成1。
- Schema：不增加必需字段；使用现有status/value/reason/confidence。风险数值不再携带NaN。有效数据结构保持兼容。
- Scan46：现有合法输入预期不变，须比较完整正式结果，而不只看总分。
- 测试：NaN/±Inf、数字字符串、布尔字符串/整数、数值位置的bool、负距离、越界ratio、None与0、非法confidence、单位/类别错配、严格JSON序列化。
- 风险等级：P0，可能违反UNKNOWN!=SAFE；没有证据说明Scan46已触发NaN问题。
- 推荐commit：`fix(risk): keep invalid metric evidence not evaluable`。

### Q02 — P0：导入载荷缺项时分母可能缩小（首批修复）

- 位置：`risk_assessment.py:159,206,291,333`；`spatial_metrics.py:146`。
- 当前行为：标准构建器要求15项，但`build_risk_assessment`直接接受部分JSON；评分/覆盖均按传入列表长度计算，缺项会从分母消失。
- 原因：文件入口与标准构建器校验不统一；重复项也可能重复计分。
- 修改：正式assessment入口补齐缺项为not_evaluable，拒绝重复/未知代码；使用规范化后的同一组指标计算评分、覆盖率和输出。
- Schema：仍15项目录和现有字段；新增missing reason值；不改变40/30/30或阈值。
- Scan46：完整15项载荷应完全不变。
- 测试：空目录、只提供核心项、缺一个类别、重复项、陈旧coverage字段、输入对象不被修改。
- 风险等级：P0，损坏/截断载荷可能产生虚假完整度。
- 推荐commit：与Q01同批，因为两者共同构成正式输入校验边界。

### Q03 — P1：主路径由列表顺序决定，缺证据可能被表达为阻塞/无阻塞

- 位置：`spatial_paths.py:12,51,83`；`space_foundation.py:454-455`。
- 当前行为：主路径若已存在于passages中，不被移到首位；extract选择首个非活动区路径。已复现secondary排在前面时替代primary。
- 原因：缺少显式主路径身份选择；`bool(None)`以及path_exists推导的状态可能掩盖证据缺失。
- 修改：依据明确primary id选路径；布尔字段保持三态。区分无法建立输入与有充分证据的真实阻塞。
- Schema：优先保持路径字段，必要时仅增加可选主路径标记；不新建几何路径。
- Scan46：当前只有主路径，预期不变；不能声称其已发生轨迹错选。
- 测试：多路径重排序、缺id、缺path_blocked、true/false/null、缺门床、已证实blocked。
- 风险等级：P1；正常生产上游通常同时提供两个布尔值，但缺输入时顶层not_evaluable与route布尔值仍需一致。
- 推荐commit：`fix(paths): preserve primary identity and unknown evidence`。

### Q04 — P1：全局最窄宽度的定位与来源不匹配

- 位置：`spatial_metrics.py:181`。
- 当前行为：从其他路径选0.5m最小值，却保留0.8m主路径的坐标和confidence；已用最小双路径输入复现。
- 原因：只收集width数值，未携带产生最小值的完整route证据。
- 修改：最小值连同path_id、瓶颈坐标、confidence、source一起选取；仅在路径证据可靠时纳入。
- Schema：字段不变，修正内容；仅依赖主路径时source不应声称另一文件提供。
- Scan46：两项同源，不应改变原值；未来多路径图会修正定位。
- 测试：不同最小路径、同值、无坐标、blocked/unknown路径、真实0宽度。
- 风险等级：P1，风险图可能指错位置。
- 推荐commit：`fix(metrics): retain minimum passage provenance`。

### Q05 — P1：门/入口证据口径存在缺口

- 位置：`spatial_metrics.py:231,260`；`space_foundation.py:269,311`。
- 当前行为：入口id未匹配时，如果仅有一扇门会选该门；入口可用空间实际是整个门连通区域，不是门边转身区域。结构路径生成还有`width_m or 0.8`兜底。
- 原因：入口身份、连通面积和局部入口操作空间不是同一个概念；缺失或0宽度可能被替换为默认0.8。
- 修改：明确入口身份；不以默认门宽建立正式可达证据；入口面积先如实命名/说明来源，缺少局部区域数据则保留不可评估，不创造局部门区或Door Recovery。
- Schema：优先修改reason/说明，不静默改变指标定义；任何定义变更单独版本化。
- Scan46：门宽已verified，是否改变入口项必须用保存结构化数据确认，不能预判改善分数。
- 测试：入口id错配、单门无入口id、门宽None/0、未verified门、有连通面积却无局部转身区。
- 风险等级：P1，错误数据可能参与路径建模；属于结构化派生层，不触碰门检测模型。
- 推荐commit：入口身份修复与口径说明分别提交。

### Q06 — P1：重复/自配对家具污染净距与占地

- 位置：`spatial_layout.py:9,106,152,174`；`space_foundation.py`家具配对循环。
- 当前行为：`between=[bed_a,bed_a],clearance_m=0`被接受，已复现；部分函数只检查列表长度，床周边限制更弱；crowding直接累加矩形面积。
- 原因：未验证不同且非空的对象身份，重复实例可能重复计入；占地和不等于不重叠占用面积。
- 修改：统一验证关系两端，不让self-pair参与；同ID重复/冲突需要诊断，不静默挑有利结果。真实不同物体接触的0必须保留。
- Schema：现有字段不变，增加明确原因；不重识别、不改家具L/W/H。
- Scan46：已观察配对为不同ID，预期主指标不变，但需要完整数据核验；不按固定ID过滤。
- 测试：self-pair、空ID、反向重复、冲突重复、合法接触0、重复家具、重叠占地。
- 风险等级：P1；占用面积并集的定义改进宜后续单独评估。
- 推荐commit：`fix(layout): validate distinct furniture relations`，面积政策另批。

### Q07 — P1：离墙距离只检查家具角点，不能完整描述床侧净空

- 位置：`spatial_layout.py:32,61,73,106,174`。
- 当前行为：墙距取家具角点到地面多边形边的最短距离，没有处理家具边穿墙；bedside取床到最近家具的任意方向距离。
- 原因：长边中段穿越墙边时角点均可能离墙较远；床尾/床头间隙不等价于可上下床侧的净空。
- 修改：只使用已存在2D边界关系验证距离/相交；缺床侧方向证据时解释为最近家具净距，不能编造照护侧。记录wall/side/front证据，不改尺寸。
- Schema：可添加可选边界类型；不能暗改既有尺寸字段。
- Scan46：床贴墙0不能简单过滤；结果差异必须如实记录。
- 测试：长边穿墙、凹房间、旋转家具、床头最近而侧面宽、缺可靠边界。
- 风险等级：P1；人体/照护情境推理属于P3，暂不引入。
- 推荐commit：墙距正确性修复独立提交；床侧定义另批。

### Q08 — P1：改造助手活动区证据与正式链不一致

- 位置：`renovation_geometry.py:316,392,404,411-424`。
- 当前行为：房间中央60%区域被当活动区，门到床路径被用作活动区安全证据；正式主链仍要求显式活动锚点。
- 原因：同metric_code在两条路径含义不同。
- 修改：模拟也要求明确活动锚点及对应路径；不具备证据时保持not_evaluable。
- Schema：维持现有状态字段；不新增猜测坐标。
- Scan46：可能改变助手预测覆盖率及结果，不能宣称改变已存正式47.8分。
- 测试：无锚点、有锚点但无对应路径、明确活动区、助手前后正式文件hash不变。
- 风险等级：P1，当前只由助手API触发，不是正式报告写回链。
- 推荐commit：`fix(assistant): preserve unknown activity evidence`。

### Q09 — P1：助手置信度和评分口径不能直接等价为正式改造后评分

- 位置：`renovation_geometry.py:49,324-327`；`renovation_simulator.py:239-265`；`renovation_compare.py`。
- 当前行为：模拟可评估项统一confidence=0.9；正式总分加上另一套几何指标计算的分差作为after_score。无完整正式分数时还能保留模拟数字分数；unknown→low也可被列为风险改善。
- 原因：模拟/正式指标集合和覆盖不同，分差不可直接迁移；获得证据不等于消除风险。
- 修改：保留真实证据置信度；同一证据口径下输出自洽类别/总分；不可正式评估时只给定性变化；不以截断到0～100掩盖不一致。
- Schema：优先保持现有响应字段，明确simulation状态；若需额外状态仅作可选字段。
- Scan46：只影响助手预测，未发现写回正式JSON/PDF；当前API未展示内部统一90%置信度，不夸大用户可见影响。
- 测试：无动作、缺核心指标、覆盖不等、分数边界、未知到已知不计为整改改善。
- 风险等级：P1。
- 推荐commit：置信度传播、预测分一致性分别提交。

### Q10 — P1：Top Risks重复，但底层指标不能被删

- 位置：`risk_assessment.py:256,274`；Scan46现有top_risks。
- 当前行为：仅按严重度、confidence、risk_code排序；同一0.48m瓶颈占两个名额。
- 原因：没有展示层关联；同一对象不等价于同一问题，不能仅按object_id去重。
- 修改设计：仅对白名单同语义风险族聚类；要求同来源位置/路径/边界、相同单位和测值。未知位置不自动合并。组内选择最严重、证据更强的代表，保留其他risk_code的可选关联字段。先只处理主通道/最小通道同瓶颈。
- Schema：完整risks、metrics、计分保持不变；top_risks可增加兼容性元数据。
- Scan46：预计两个瓶颈合成一个展示项，总分/覆盖不变；bed贴墙与bed侧家具不合并。
- 测试：同瓶颈、不同坐标、不同路径、不同单位、缺位置、不同严重度、稳定排序、底层数组不变。
- 风险等级：P1。
- 推荐commit：`feat(risk): group duplicate bottlenecks in top risks`。注意collect_specific_advice当前复用rank_top_risks，需避免意外丢建议。

### Q11 — P1：置信度样本数及建议解释不足

- 位置：`risk_assessment.py:100,206,274`；`spatial_layout.py:174`；`rules.py`。
- 当前行为：reason为规则代码模板；建议基本按指标和severity区分，但未附位置/对象；非null confidence才参与均值。床周边使用全部床confidence最小值，不一定对应获选边界证据。
- 原因：容易把证据均值理解为所有测量可靠度；把规则模板当现场原因。
- 修改：展示confidence_sample_count及证据来源；匹配获选边界的confidence；建议仅填充已有路径/对象标签，不猜物体类型、不编造出处。
- Schema：优先现有字段及可选解释字段；不重复乘coverage。
- Scan46：明确0.850来自4项；不擅自修改其余9项为0或1。
- 测试：混合null、0置信度、两床不同证据、对象名称缺失、建议对应风险、重复覆盖修正。
- 风险等级：P1。
- 推荐commit：解释性展示与选中证据confidence修复分开。

### Q12 — P1/P3：规则来源待核实，指标定义与阈值用途需对应

- 位置：`rules.py:FORMAL_RULES/FORMAL_CATEGORY_WEIGHTS`；`pdf_report.py:_score_color`；`renovation_geometry.py:19,384-391`。
- 当前行为：正式阈值在FORMAL_RULES，reference为reference_pending；PDF另有80/60总分颜色断点；助手有0.30m障碍判断和0.90m扩门目标。
- 原因：正式阈值、几何参数、整改目标、总分颜色是不同概念，不能合称一个已获标准依据的规则源。
- 修改：保留数值，注明工程规则待验证；复用规则提供整改目标，单独标识几何参数与总分展示规则。研究权重/标准来源在后续审批中进行。
- Schema：当前不改变，规则需变更时显式版本化。
- Scan46：本轮不调整标准数值或40/30/30，不为47.8反推规则。
- 测试：所有方向阈值两侧/等值、bool等值、规则目录唯一、版本一致。
- 风险等级：P1解释一致性；正式标准确认与覆盖加权属于P3。
- 推荐commit：规则元信息及来源说明，勿与阈值调整混合。

### Q13 — P2：风险图遗漏合法位置格式，同位置标记重叠

- 位置：`formal_risk_figure.py:21,35,62`。
- 当前行为：只解析dict中的point_xy/position_xy/center_xyz/object_id；忽略object_ids配对、嵌套bottleneck及列表position；列表会调用.get导致异常。同坐标标号覆盖，低风险也绘制，未与Top Risks对应。
- 原因：位置schema允许的形式比渲染器支持范围广。
- 修改：验证位置类型和有限坐标；对象配对用已知关系线而非猜危险点；同位置聚合编号与说明；明确图示范围和无法定位数量。不得给缺位置风险画原点占位。
- Schema：不修改结构图；只改风险派生图/可选标记元数据。
- Scan46：家具配对风险目前可能缺标记，通道标号可能重叠；提高展示完整性但不改测量/评分。
- 测试：列表/null/嵌套位置、多对象、无坐标、同位置、PNG生成及布局检查。
- 风险等级：P2；位置类型异常导致图缺失为功能缺陷。
- 推荐commit：位置解析修复与标签布局分别提交。

### Q14 — P2：PDF入口无法按当前Web打开方式访问

- 位置：`app/routers/reports.py:17,77`；`app/deps.py:13`；`report_page.dart:204-216`。
- 当前行为：通用`/{scan_id}/{filename}`注册在`/{scan_id}/pdf`之前，会遮挡PDF路由；Flutter新标签页发送?token，后端只读Authorization头。之前截图401与该代码一致。
- 原因：artifact解析和按钮存在测试没有覆盖点击后的真实HTTP鉴权/路由。
- 修改：先确保PDF专用路径不被遮挡；优先通过已鉴权请求获取PDF再打开blob，或设计仅限artifact的短期访问凭据；不移除机构权限检查，不把长期登录token扩展到所有查询参数鉴权。
- Schema：使用已有PDF URL，保持空值降级；路由兼容旧URL。
- Scan46：只修复文件访问，不重新生成风险。
- 测试：按钮实际请求、401/过期凭据、同机构200 PDF、跨机构拒绝、缺文件404、null/空URL。
- 风险等级：P2用户功能不可用；没有权限绕过证据。
- 推荐commit：`fix(reports): open authenticated PDF artifacts`。

### Q15 — P2：PDF/Flutter对未知状态和信息层级表达不足

- 位置：`pdf_report.py:147`；`report_page.dart:172-203,566`；`risk_card.dart`。
- 当前行为：PDF未使用top_risks，测量表先于风险摘要，正式status未突出；空建议写“未发现需要整改的高/中风险”。Flutter空Top列表写“未发现中高风险”，空建议写“无需改造建议”，即使证据不足。比例显示函数按<=1猜单位，可能将coverage=1%显示100%。
- 原因：空结果与安全结果没有在所有展示分支区分；percentage与ratio共用启发式格式化。
- 修改：首页显示backend score/status/confidence/coverage和3～6条Top；无证据时提示无法判断；按字段单位格式化，不由Flutter重算风险。布尔量展示为是/否，不原样true/false。
- Schema：不改变backend分数或阈值，不新建重复模型。
- Scan46：报告更易解释，已知数值保持不变。
- 测试：全未知、空建议、partial coverage、0%/1%/86.7%、0/1置信度、PDF文本/布局和Top一致性。
- 风险等级：P2；未知被语言暗示为安全属于必须修复的展示问题。
- 推荐commit：未知空状态、比例格式化、PDF信息层级分别提交。

### Q16 — P1：JSON、比较接口和静态artifact可能使用不同版本

- 位置：`app/routers/reports.py:get_report/compare`；`pipeline_runner.py:_build_formal_pdf`。
- 当前行为：report优先读新risk JSON；compare仍读数据库Report快照；风险图/PDF是已生成文件，不随API读入更新。
- 原因：局部重建结构化报告后，数据源优先级和artifact版本没有统一验证。
- 修改：报告读取共享单一有效版本/指纹；现有文件不匹配时标记待生成，不用新分数配旧PDF。不得在GET中偷偷重算风险。
- Schema：可增加可选artifact版本/可用状态；保存现有URL字段。
- Scan46：当前一致性需以现有文件验收；不假设其已发生版本漂移。
- 测试：JSON更新但DB旧、artifact缺失/过期、compare与详情一致、旧schema无版本兼容。
- 风险等级：P1，可能误解释改造效果。
- 推荐commit：统一报告读取；artifact版本验证另批。

## 4. Legacy分类

| 组件 | 分类 | 调用证据与处理 |
| --- | --- | --- |
| FORMAL_RULES、正式risk_assessment | ACTIVE | pipeline与助手均调用正式评分器；保留并加强输入边界 |
| spatial_metrics/paths/layout | ACTIVE | 正式结构化数据链使用；不替换上游模型 |
| rules.RULES、WEIGHTS=40/40/20、compute_score | LEGACY_BUT_SAFE | 当前main生产调用搜索未发现外部调用，现有测试/兼容仍引用；不删除 |
| report_builder.render_annotation_images | DEAD_CODE（当前生产调用图）/POTENTIAL_CONFLICT | 未发现生产调用；按点序号着色而非证据位置，禁止接回正式风险图 |
| report_builder.build_preview_assets | LEGACY_BUT_SAFE | 预览资源构造能力不等于风险评分，当前调用范围需迁移前再核验 |
| report_composer.build_risk_geometries | LEGACY_BUT_SAFE/POTENTIAL_CONFLICT | 正式risks无legacy code且points=None，因此未走原点占位；legacy分支仍有默认原点，不能作为正式定位 |
| visual_risks | LEGACY_BUT_SAFE | 当前生产调用搜索没有外部analyze调用；本轮不接入视觉模型 |
| renovation_geometry.compute_metrics | ACTIVE/DUPLICATED/POTENTIAL_CONFLICT | 助手真实调用，多个metric含义与正式链不同；不把模拟当正式报告 |
| Flutter legacy框架、PDF legacy measure表 | LEGACY_BUT_SAFE/展示重复 | 正式页面已有分支，PDF部分旧表仍显示；按schema兼容保留后再小步整理 |

分类依据是6795677的仓库调用搜索，不代表动态导入或外部脚本绝无调用；删除任何模块前应另做依赖核验。

## 5. 测试覆盖审计

| 场景 | 现状 | 本轮/后续 |
| --- | --- | --- |
| None vs 0/False | 有部分missing与bool测试，缺成套边界 | 首批扩展 |
| 阈值等值 | 已覆盖door=0.8、crowding=0.6，未覆盖全部规则 | 后续参数化全部方向 |
| 全缺失/一类别缺失/截断目录 | 部分core缺失测试；导入入口覆盖不足 | 首批扩展 |
| partial coverage/confidence | 已有排除未知和独立计算测试 | 首批增加陈旧coverage防伪及非法confidence |
| 重复Top风险 | 无同瓶颈行为测试 | Q10 |
| 重复object ID/self-pair | 缺失 | Q06 |
| activity missing/present | 正式链已有，助手缺对应约束 | Q08 |
| artifact null/空/缺字段 | Flutter models已有 | 增加真正点击和HTTP鉴权 Q14 |
| old schema compatibility | models已有 | 每批保留 |
| position序列化 | 基础dict已有，列表/嵌套/NaN缺失 | Q13 |
| 风险PDF | 文件头/文件大小/部分行值测试 | 增加文本、页层级、未知提示；不能只测%PDF |

历史无关失败仅作为用户提供的清单保留：measurement_builder两项尺度测试、semantic_evidence关键帧测试、structure_builder未标签地面障碍测试，以及Flutter历史报告页测试。最新main已删除其中部分模块/测试，所以不沿用旧314/4和55/6作为当前运行结论，也不修复这些无关历史问题。

## 6. 实施顺序与合并准入

1. **首批Q01/Q02**：有限数/真正布尔/状态单位及目录校验，unknown不能流入低风险。先异常输入targeted tests，再正式风险相关回归，再只用Scan46旧spatial_metrics验证完整输出无退化。
2. **下一批Q03/Q04/Q06**：每个提交独立修复主路径、瓶颈定位、家具身份关系；按顺序回归，同批不得修改阈值。
3. **助手批Q08/Q09**：保持显式活动证据，统一预测证据口径；独立标识模拟输出，不写回真实文件。
4. **解释批Q10/Q11/Q16**：Top展示聚类、置信度样本/建议说明、版本一致性；全部metrics/risks不因展示去重而减少。
5. **体验批Q13/Q14/Q15**：PDF访问、未知空状态、风险图位置支持与标签、PDF首页层级；逐项后端/Flutter实际访问验证。
6. **P3**：活动功能分区、床侧照护语义、占用面积定义、规则正式来源及覆盖权重政策；需要新的明确结构证据或产品决策，暂不实施。

每个提交只能包含对应修复及必要测试。合并前检查main最新提交；若有新提交先同步并复测，不force push。合法Scan46结果不应因输入校验改变；如改变必须查明原因，不能修改Scan46专属参数掩盖。

## 7. 首批验证记录

- 风险与结构化评估相关回归：212 passed。
- 同一回归命令另有4个`test_renovation_assistant.py`失败；在未修改的6795677基线独立复测同样为4 failed、3 passed，均是测试与最新助手能力/API签名不一致，不是首批新增失败。本轮未修改助手。
- 首批新增异常/目录测试109个，覆盖无效有限数、布尔类型、置信度、单位/类别/状态、严格JSON、完整目录、0与False边界及输入不可变。
- Scan46使用既有`spatial_metrics.json`内存重放：风险评估除文件构建器的`metric_input`元数据外，与已验收`risk_assessment.json`全部section一致；47.8分、13/15、86.7%、0.850/0.737、15项风险均不变。
- Scan46输入SHA256为`6d2c3497c9c9e4bc8b9efcd86572ab65422fd5d4db50c782b5fbc57662e3a0db`，验证前后未修改。
- `git diff --check`通过。首批新增失败为0。

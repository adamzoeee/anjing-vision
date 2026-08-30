import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../api/client.dart';
import '../api/models.dart';
import '../widgets/risk_card.dart';
import '../widgets/score_gauge.dart';
import 'preview_launcher.dart';

NetworkImage authenticatedReportImage(ApiClient api, String path) =>
    NetworkImage(
      '${api.dio.options.baseUrl}$path',
      headers: api.authorizationHeaders,
    );

class ReportPage extends StatefulWidget {
  final Scan scan;
  const ReportPage({super.key, required this.scan});
  @override
  State<ReportPage> createState() => _ReportPageState();
}

class _ReportPageState extends State<ReportPage> {
  Report? _report;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await context.read<ApiClient>().report(widget.scan.id);
      if (!mounted) return;
      setState(() {
        _report = r;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = '$e');
    }
  }

  @override
  Widget build(BuildContext context) {
    final r = _report;
    final api = context.read<ApiClient>();
    return Scaffold(
      appBar: AppBar(title: const Text('评估报告')),
      body: _error != null
          ? Center(child: Text(_error!))
          : r == null
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  if (r.score != null)
                    Center(child: ScoreGauge(score: r.score!))
                  else
                    const Center(
                      child: Padding(
                        padding: EdgeInsets.all(12),
                        child: Text(
                          '⚠ 关键测量项缺失，暂无法评分',
                          style: TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                    ),
                  const SizedBox(height: 8),
                  Center(
                    child: Text(
                      r.calibrated == 3
                          ? '已按多个已知物体标定真实尺寸'
                          : r.calibrated == 1
                          ? '已按 A4 纸标定真实尺寸'
                          : r.calibrated == 2
                          ? '已按门高先验标定（精度较低）'
                          : '⚠ 未完成尺寸标定，结果仅供参考',
                      style: const TextStyle(fontSize: 12, color: Colors.grey),
                    ),
                  ),
                  const SizedBox(height: 16),
                  ..._formalAssessmentSummary(context, r),
                  ..._keyMetricsSection(context, r),
                  const SizedBox(height: 16),
                  _structurePlanSection(context, api, widget.scan.id),
                  const SizedBox(height: 16),
                  _passagePlanSection(context, api, widget.scan.id),
                  const SizedBox(height: 16),
                  _metricFrameworkSection(context, r),
                  const SizedBox(height: 16),
                  ..._furnitureDetailSection(context, r),
                  const SizedBox(height: 16),
                  Text('尺寸信息', style: Theme.of(context).textTheme.titleMedium),
                  ..._measurementTiles(r),
                  const SizedBox(height: 16),
                  Text('重建质量', style: Theme.of(context).textTheme.titleMedium),
                  ..._qualityTiles(r),
                  const SizedBox(height: 16),
                  Text(
                    '风险项（${_visibleRisks(r).length}）',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  if (_visibleRisks(r).isEmpty)
                    Padding(
                      padding: EdgeInsets.all(8),
                      child: Text(r.riskAssessment['official'] == true
                          ? '未发现中高风险项'
                          : '未检测到风险项'),
                    )
                  else
                    ..._visibleRisks(r).map((risk) => RiskCard(risk: risk)),
                  ..._notEvaluableSection(context, r),
                  const SizedBox(height: 16),
                  Text('改造建议', style: Theme.of(context).textTheme.titleMedium),
                  if (r.advice.isEmpty)
                    const Padding(
                      padding: EdgeInsets.all(8),
                      child: Text('无需改造建议'),
                    )
                  else
                    ...r.advice.map(
                      (a) => ListTile(
                        leading: const Icon(Icons.build),
                        title: Text(a),
                      ),
                    ),
                  if (r.reportPdf?.trim().isNotEmpty ?? false) ...[
                    const SizedBox(height: 8),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: OutlinedButton.icon(
                        onPressed: () {
                          final token = Uri.encodeComponent(api.token ?? '');
                          final separator = r.reportPdf!.contains('?') ? '&' : '?';
                          final pdfUrl =
                              '${api.dio.options.baseUrl}${r.reportPdf}'
                              '${separator}token=$token';
                          openPreview(context, pdfUrl);
                        },
                        icon: const Icon(Icons.picture_as_pdf),
                        label: const Text('打开正式评估 PDF'),
                      ),
                    ),
                  ],
                  const SizedBox(height: 16),
                  Text(
                    '3D 场景预览',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 8),
                  Container(
                    height: 200,
                    width: double.infinity,
                    color: Colors.black,
                    alignment: Alignment.center,
                    child: r.previewViewer == null
                        ? const Text(
                            '暂无 3D 预览',
                            style: TextStyle(color: Colors.white54),
                          )
                        : ElevatedButton.icon(
                            onPressed: () {
                              // 新 3D 预览：高密度点云 + SpatialLM 墙/门/窗/家具框
                              final token = Uri.encodeComponent(
                                api.token ?? '',
                              );
                              final previewUrl =
                                  '${api.dio.options.baseUrl}${r.previewViewer}'
                                  '?scan=${r.scanId}&token=$token';
                              openPreview(context, previewUrl);
                            },
                            icon: const Icon(Icons.view_in_ar),
                            label: const Text('打开 3D 预览'),
                          ),
                  ),
                  const SizedBox(height: 16),
                  Text('标注视图', style: Theme.of(context).textTheme.titleMedium),
                  if (r.images.isEmpty)
                    const Padding(
                      padding: EdgeInsets.all(8),
                      child: Text('暂无标注图'),
                    )
                  else
                    ...r.images.map(
                      (img) => Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        child: Image(
                          image: authenticatedReportImage(api, img),
                          errorBuilder: (_, _, _) =>
                              const Icon(Icons.broken_image, size: 48),
                        ),
                      ),
                    ),
                ],
              ),
            ),
    );
  }

  Widget _structurePlanSection(BuildContext context, ApiClient api, int scanId) {
    final url =
        '${api.dio.options.baseUrl}/api/preview/$scanId/structure_plan.png?v=20260822';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('2.5D 结构图（按测量结果绘制）',
            style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 8),
        ClipRRect(
          borderRadius: BorderRadius.circular(10),
          child: Image.network(
            url,
            headers: api.authorizationHeaders,
            errorBuilder: (context, error, stack) => const Padding(
              padding: EdgeInsets.all(12),
              child: Text('结构图尚未生成'),
            ),
            loadingBuilder: (context, child, progress) => progress == null
                ? child
                : const Padding(
                    padding: EdgeInsets.all(24),
                    child: Center(child: CircularProgressIndicator()),
                  ),
          ),
        ),
      ],
    );
  }

  Widget _passagePlanSection(BuildContext context, ApiClient api, int scanId) {
    final url =
        '${api.dio.options.baseUrl}/api/preview/$scanId/passage_plan.png?v=20260822';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('通行图（结构平面 + 门→床路径与通道净宽标注）',
            style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 8),
        ClipRRect(
          borderRadius: BorderRadius.circular(10),
          child: Image.network(
            url,
            headers: api.authorizationHeaders,
            errorBuilder: (context, error, stack) => const Padding(
              padding: EdgeInsets.all(12),
              child: Text('通行图尚未生成'),
            ),
            loadingBuilder: (context, child, progress) => progress == null
                ? child
                : const Padding(
                    padding: EdgeInsets.all(24),
                    child: Center(child: CircularProgressIndicator()),
                  ),
          ),
        ),
      ],
    );
  }

  /// 风险项过滤：门槛/台阶/坡度/不平/卫生间门口暂不评估，先不显示。
  static const Set<String> _hiddenRiskCodes = {
    'threshold', 'stairs', 'slope', 'uneven', 'bathroom_door',
  };

  List<Risk> _visibleRisks(Report report) {
    if (report.riskAssessment['official'] == true) {
      return (report.riskAssessment['top_risks'] as List? ?? const [])
          .whereType<Map>()
          .map((item) => Risk.fromJson(Map<String, dynamic>.from(item)))
          .toList();
    }
    return report.risks
        .where((risk) => !_hiddenRiskCodes.contains(risk.code))
        .toList();
  }

  List<Widget> _formalAssessmentSummary(BuildContext context, Report report) {
    final assessment = report.riskAssessment;
    if (assessment['official'] != true) return const [];
    final overall = assessment['overall'] is Map
        ? assessment['overall'] as Map
        : const {};
    final confidence = overall['confidence'];
    final coverage = overall['coverage_percent'];
    final categories = assessment['category_scores'] is Map
        ? assessment['category_scores'] as Map
        : const {};
    const names = {
      'mobility': '通行能力',
      'layout': '空间布局',
      'usage_safety': '使用安全',
    };
    String percent(Object? value) => value is num
        ? '${(value <= 1 ? value * 100 : value).toStringAsFixed(1)}%'
        : '—';
    return [
      Card(
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('正式空间风险评估', style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 6),
              Text('评估覆盖率：${percent(coverage)}'),
              Text('综合置信度：${percent(confidence)}'),
              if (overall['status'] == 'insufficient_data')
                const Text('核心证据不足，本次不生成综合分数'),
            ],
          ),
        ),
      ),
      const SizedBox(height: 8),
      Text('分类评分', style: Theme.of(context).textTheme.titleMedium),
      const SizedBox(height: 6),
      ...names.entries.map((entry) {
        final data = categories[entry.key] is Map ? categories[entry.key] as Map : const {};
        final score = data['score'];
        final weight = data['weight'];
        return ListTile(
          dense: true,
          leading: const Icon(Icons.analytics_outlined),
          title: Text('${entry.value}（${percent(weight)}）'),
          trailing: Text(score is num ? score.toStringAsFixed(1) : '不可评估'),
        );
      }),
      const SizedBox(height: 8),
    ];
  }

  List<Widget> _notEvaluableSection(BuildContext context, Report report) {
    if (report.riskAssessment['official'] != true) return const [];
    final items = (report.riskAssessment['not_evaluable'] as List? ?? const [])
        .whereType<Map>()
        .map((item) => Risk.fromJson(Map<String, dynamic>.from(item)))
        .toList();
    if (items.isEmpty) return const [];
    return [
      const SizedBox(height: 16),
      Text('不可评估项（${items.length}）', style: Theme.of(context).textTheme.titleMedium),
      ...items.map((risk) => RiskCard(risk: risk)),
    ];
  }

  /// 评价框架（只改指标名称与分组，不评分——评分由下一步实现）：
  /// 通行能力 40% / 空间布局 30% / 使用安全 30%。
  Widget _metricFrameworkSection(BuildContext context, Report report) {
    if (report.riskAssessment['official'] == true) {
      return _formalMetricFrameworkSection(context, report);
    }
    final raw = report.measures['measurements'];
    final m = raw is Map ? raw : const {};
    final passage = m['passage'] is Map ? m['passage'] as Map : const {};
    final walkable = m['walkable_area_m2'];
    final openings = m['openings'] is List ? m['openings'] as List : const [];
    final door = openings.whereType<Map>().where((o) => o['type'] == 'door').firstOrNull;
    final distances = m['distances'] is List ? m['distances'] as List : const [];
    final nearest = distances.whereType<Map>().firstWhere(
          (d) => (d['clearance_m'] is num) && (d['clearance_m'] as num) > 0.001,
          orElse: () => distances.isNotEmpty ? distances.first as Map : const {},
        );
    String fmt(Object? value) =>
        value is num ? value.toStringAsFixed(2) : (value?.toString() ?? '—');

    final groups = <(String, String, List<String>)>[
      ('通行能力', '占比 40%', [
        if (passage['passage_width_m'] != null)
          '通道宽度 ${fmt(passage['passage_width_m'])}m',
        if (passage['path_length_m'] != null)
          '主要路径长度 ${fmt(passage['path_length_m'])}m',
        if (door != null) '门口空间：门宽 ${fmt(door['width_m'])}m',
        '路径阻碍情况：通道障碍物',
      ]),
      ('空间布局', '占比 30%', [
        if (nearest.isNotEmpty)
          '家具间距（最近）${fmt(nearest['clearance_m'])}m',
        if (walkable is num) '活动面积 ${walkable.toStringAsFixed(1)}m²',
        '拥挤程度',
      ]),
      ('使用安全', '占比 30%', [
        '床周围空间',
        '主要活动区域',
      ]),
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(report.riskAssessment['official'] == true ? '关键空间指标' : '空间评价框架（待评分）',
            style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 8),
        ...groups.map((group) {
          final (name, weight, items) = group;
          return Card(
            margin: const EdgeInsets.only(bottom: 8),
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('$name（$weight）',
                      style: Theme.of(context).textTheme.titleSmall),
                  const SizedBox(height: 6),
                  ...items.map((item) => Padding(
                        padding: const EdgeInsets.only(bottom: 2),
                        child: Text('· $item',
                            style: Theme.of(context).textTheme.bodyMedium),
                      )),
                ],
              ),
            ),
          );
        }),
      ],
    );
  }

  Widget _formalMetricFrameworkSection(BuildContext context, Report report) {
    final raw = report.riskAssessment['key_metrics'] as List? ?? const [];
    final metrics = raw.whereType<Map>().toList();
    const categoryNames = {
      'mobility': '通行能力',
      'layout': '空间布局',
      'usage_safety': '使用安全',
    };
    String valueText(Map metric) {
      if (metric['status'] == 'not_evaluable') {
        return '不可评估：${metric['reason'] ?? '数据不足'}';
      }
      final value = metric['value'];
      final formatted = value is num ? value.toStringAsFixed(2) : value.toString();
      final unit = metric['unit']?.toString() ?? '';
      return '$formatted${unit.isEmpty ? '' : ' $unit'}';
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('关键空间指标', style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 8),
        ...categoryNames.entries.map((entry) {
          final items = metrics.where((metric) => metric['category'] == entry.key).toList();
          if (items.isEmpty) return const SizedBox.shrink();
          return Card(
            margin: const EdgeInsets.only(bottom: 8),
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(entry.value, style: Theme.of(context).textTheme.titleSmall),
                  const SizedBox(height: 6),
                  ...items.map((metric) => Padding(
                        padding: const EdgeInsets.only(bottom: 3),
                        child: Text('${metric['name']}：${valueText(metric)}'),
                      )),
                ],
              ),
            ),
          );
        }),
      ],
    );
  }

  List<Widget> _keyMetricsSection(BuildContext context, Report report) {
    final raw = report.measures['measurements'];
    final m = raw is Map ? raw : const {};
    final room = m['room'] is Map ? m['room'] as Map : const {};
    final scale = m['scale'] is Map ? m['scale'] as Map : const {};
    final openings = m['openings'] is List ? m['openings'] as List : const [];
    final door = openings.whereType<Map>().where((o) => o['type'] == 'door').firstOrNull;
    final validation = m['quality'] is Map && m['quality']['validation'] is List
        ? m['quality']['validation'] as List
        : const [];
    if (m.isEmpty && validation.isEmpty) return const [];

    String fmt(Object? value) =>
        value is num ? '${value.toStringAsFixed(2)}m' : (value?.toString() ?? '—');
    final rows = <Widget>[
      if (room.isNotEmpty)
        ListTile(
          dense: true,
          leading: const Icon(Icons.home_outlined),
          title: const Text('房间尺寸'),
          subtitle: Text(
            '长 ${fmt(room['length_m'])} × 宽 ${fmt(room['width_m'])} × 高 ${fmt(room['height_m'])}',
          ),
        ),
      if (door != null)
        ListTile(
          dense: true,
          leading: const Icon(Icons.door_front_door_outlined),
          title: const Text('门洞净尺寸'),
          subtitle: Text('宽 ${fmt(door['width_m'])} × 高 ${fmt(door['height_m'])}'),
        ),
    ];
    final passage = m['passage'] is Map ? m['passage'] as Map : const {};
    final walkable = m['walkable_area_m2'];
    if (passage.isNotEmpty && passage['status'] == 'ok') {
      rows.add(ListTile(
        dense: true,
        leading: const Icon(Icons.directions_walk_outlined),
        title: const Text('通道与可行走'),
        subtitle: Text(
          '最窄通道 ${fmt(passage['passage_width_m'])} · 门→床路径 ${fmt(passage['path_length_m'])}'
          '${walkable is num ? ' · 可行走 ${walkable.toStringAsFixed(1)}m²' : ''}',
        ),
      ));
    }
    final distances = m['distances'] is List ? m['distances'] as List : const [];
    if (distances.isNotEmpty) {
      final nearest = distances.whereType<Map>().firstWhere(
            (d) => (d['clearance_m'] is num) && (d['clearance_m'] as num) > 0.001,
            orElse: () => distances.first as Map,
          );
      rows.add(ListTile(
        dense: true,
        leading: const Icon(Icons.square_foot_outlined),
        title: const Text('家具净距（最近）'),
        subtitle: Text(
          '${(nearest['between'] as List).join(' ↔ ')}：'
          '${(nearest['clearance_m'] as num).toStringAsFixed(2)}m',
        ),
      ));
    }
    final scaleOk = scale['status']?.toString() == 'metric_references';
    rows.add(ListTile(
      dense: true,
      leading: Icon(scaleOk ? Icons.straighten : Icons.info_outline,
          color: scaleOk ? Colors.green : Colors.orange),
      title: Text(scaleOk ? '真实尺寸标定：成功' : '真实尺寸标定：未完成'),
      subtitle: Text(
        scaleOk
            ? '比例系数 ${((scale['scale'] ?? 0) as num).toStringAsFixed(3)}'
                '（参考值换算一致度 ${(((scale['max_relative_disagreement'] ?? 0) as num) * 100).toStringAsFixed(1)}%）'
            : (scale['reason']?.toString() ?? '需要至少两个一致的实测参考尺寸'),
      ),
    ));
    if (validation.isNotEmpty) {
      rows.add(Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Text('测量验收（未参与标定的真值对比）',
            style: Theme.of(context).textTheme.labelLarge),
      ));
      for (final check in validation.whereType<Map>()) {
        final labels = {'bed': '床', 'table': '书桌', 'door': '门'};
        final dims = {'length': '长', 'width': '宽', 'height': '高'};
        final name =
            '${labels[check['object_type']?.toString()] ?? check['object_type']}'
            '${dims[check['dimension']?.toString()] ?? check['dimension']}';
        final predicted = check['predicted_m'];
        final actual = check['meters'];
        final rel = check['relative_error'];
        rows.add(ListTile(
          dense: true,
          title: Text(name),
          subtitle: Text(
            predicted == null
                ? '未测出'
                : '预测 ${(predicted as num).toStringAsFixed(2)}m / 实测 '
                    '${(actual as num).toStringAsFixed(2)}m / 误差 '
                    '${((rel as num) * 100).toStringAsFixed(1)}%',
          ),
        ));
      }
    }
    return rows;
  }

  List<Widget> _furnitureDetailSection(BuildContext context, Report report) {
    final raw = report.measures['measurements'];
    final m = raw is Map ? raw : const {};
    final objects = m['objects'] is List ? m['objects'] as List : const [];
    if (objects.isEmpty) return const [];
    const cn = {
      'bed': '床', 'wardrobe': '衣柜', 'sofa': '沙发', 'desk': '书桌', 'table': '桌子',
      'cabinet': '柜子', 'bookshelf': '书架', 'chair': '椅子', 'stool': '凳子',
      'small_table': '小桌', 'chandelier': '吊灯', 'carpet': '地毯', 'curtain': '窗帘',
    };
    const nums = ['一', '二', '三', '四', '五', '六'];
    final counted = <String, int>{};
    final tiles = <Widget>[
      Text('家具详情', style: Theme.of(context).textTheme.titleMedium),
    ];
    for (final item in objects.whereType<Map>()) {
      String fmt(Object? value) =>
          value is num ? value.toStringAsFixed(2) : (value?.toString() ?? '—');
      final type = item['type']?.toString() ?? item['label']?.toString() ?? '物品';
      final index = counted[type] ?? 0;
      counted[type] = index + 1;
      final name = '${cn[type] ?? type}${nums[index < nums.length ? index : nums.length - 1]}';
      final confidence = item['confidence']?.toString() ?? 'unknown';
      final confText = switch (confidence) {
        'high' => '高',
        'medium' => '中',
        _ => '低',
      };
      tiles.add(ListTile(
        dense: true,
        leading: const Icon(Icons.chair_outlined),
        title: Text('$name：长 ${fmt(item['length_m'])}m × 宽 '
            '${fmt(item['width_m'])}m × 高 ${fmt(item['height_m'])}m'),
        subtitle: Text('测量置信度：$confText'),
      ));
    }
    return tiles;
  }

  List<Widget> _measurementTiles(Report report) {
    final raw = report.measures['reference_measurements'] as List? ?? const [];
    final calibrationRaw = report.measures['calibration_quality'];
    final calibration = calibrationRaw is Map ? calibrationRaw : const {};
    final calibrationReferences = calibration['references'] is List
        ? calibration['references'] as List
        : const [];
    final labels = {
      'bed': '床',
      'table': '桌子',
      'door': '门',
      'sofa': '沙发',
      'cabinet': '柜子',
      'bookshelf': '书架',
    };
    final dimensions = {'length': '长', 'width': '宽', 'height': '高'};
    final calibrationSucceeded = report.calibrated == 3;
    final usedCount = calibration['used_count'] is num
        ? (calibration['used_count'] as num).toInt()
        : 0;
    final failureReason = calibration['reason']?.toString();
    final tiles = <Widget>[
      ListTile(
        dense: true,
        leading: Icon(
          calibrationSucceeded ? Icons.check_circle : Icons.info_outline,
          color: calibrationSucceeded ? Colors.green : Colors.orange,
        ),
        title: Text(calibrationSucceeded ? '尺度标定：成功' : '尺度标定：失败'),
        subtitle: Text(
          calibrationSucceeded
              ? '已使用 $usedCount 个参考尺寸恢复米制比例'
              : (failureReason ?? '未能用至少两个一致且成功识别的参考尺寸恢复米制比例'),
        ),
      ),
    ];
    for (final item in raw.whereType<Map>()) {
      final object = item['object_type']?.toString();
      final dimension = item['dimension']?.toString();
      final meters = item['meters'];
      if (object == null || dimension == null || meters == null) continue;
      Map? detail;
      for (final candidate in calibrationReferences.whereType<Map>()) {
        if (candidate['object_type']?.toString() == object &&
            candidate['dimension']?.toString() == dimension) {
          detail = candidate;
          break;
        }
      }
      final status = detail?['status']?.toString();
      final statusText = switch (status) {
        'used' => '已识别并用于尺度标定',
        'not_detected' => '未在重建结果中识别到对应参考物',
        'outlier' => '已识别，但推导比例与其他参考不一致，未采用',
        _ => calibrationSucceeded ? '用户输入参考值' : '用户输入参考值（未用于米制标定）',
      };
      tiles.add(
        ListTile(
          dense: true,
          leading: const Icon(Icons.straighten),
          title: Text(
            '${labels[object] ?? object}${dimensions[dimension] ?? dimension}',
          ),
          trailing: Text('${(meters as num).toStringAsFixed(2)} m'),
          subtitle: Text(statusText),
        ),
      );
    }
    final room = report.measures['room_dimensions'] as Map?;
    if (room?['status'] == 'measured') {
      tiles.add(_dimensionTile('房间', room!));
    } else if (room != null) {
      tiles.add(
        const ListTile(
          dense: true,
          leading: Icon(Icons.help_outline),
          title: Text('房间尺寸'),
          subtitle: Text('当前重建不足以可靠测量，结果标记为未知'),
        ),
      );
    }
    // 第二阶段语义空间优先：实例级卡片（含门洞宽高、置信度、支持视角）。
    final semantic = report.measures['semantic_space'] as Map?;
    final semanticObjects = semantic?['objects'];
    if (semanticObjects is List && semanticObjects.isNotEmpty) {
      for (final entry in semanticObjects.whereType<Map>()) {
        if (entry['status'] != 'measured') continue;
        tiles.add(_semanticObjectTile(entry));
      }
    } else {
      final objects = report.measures['object_dimensions'] as Map?;
      if (objects != null) {
        for (final entry in objects.entries) {
          final result = entry.value;
          if (result is Map && result['status'] == 'measured') {
            tiles.add(_dimensionTile(entry.key.toString(), result));
          }
        }
      }
    }
    if (raw.isEmpty) {
      tiles.add(
        const Padding(padding: EdgeInsets.all(8), child: Text('未记录用户参考尺寸')),
      );
    }
    final extent = report.measures['reconstruction_extent_m'] as List?;
    if (extent != null && extent.length >= 3 && report.calibrated == 3) {
      tiles.add(
        ListTile(
          dense: true,
          leading: const Icon(Icons.view_in_ar),
          title: const Text('重建空间包围尺寸（长 × 宽 × 高）'),
          trailing: Text(
            extent
                .take(3)
                .map((v) => '${(v as num).toStringAsFixed(2)}m')
                .join(' × '),
          ),
        ),
      );
    }
    return tiles;
  }

  Widget _dimensionTile(String label, Map result) {
    final values = result['dimensions'] as Map? ?? const {};
    final names = {
      'length': '长',
      'width': '宽',
      'height': '高',
      'thickness': '厚',
    };
    final valueText = values.entries
        .where((entry) => entry.value is num)
        .map(
          (entry) =>
              '${names[entry.key] ?? entry.key} ${(entry.value as num).toStringAsFixed(2)}m',
        )
        .join(' · ');
    return ListTile(
      dense: true,
      leading: const Icon(Icons.architecture),
      title: Text('$label自动测量'),
      subtitle: Text('置信度：${result['confidence'] ?? 'unknown'}'),
      trailing: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 240),
        child: Text(valueText, textAlign: TextAlign.end),
      ),
    );
  }

  String _confidenceZh(String? value) => switch (value) {
    'high' => '高',
    'medium' => '中',
    'low' => '低',
    _ => '未知',
  };

  Widget _semanticObjectTile(Map object) {
    final instanceId = object['instance_id']?.toString() ?? '';
    final label = object['label']?.toString() ?? '';
    final dims = object['dimensions'] is Map
        ? object['dimensions'] as Map
        : const {};
    final metadata = object['metadata'] is Map
        ? object['metadata'] as Map
        : const {};
    final door = metadata['door_measurement'] is Map
        ? metadata['door_measurement'] as Map
        : const {};
    final supportingViews = object['supporting_views'];
    final confidence = _confidenceZh(
      object['measurement_confidence']?.toString(),
    );

    String? fmt(String key) {
      final value = dims[key];
      return value is num ? value.toStringAsFixed(2) : null;
    }

    String valueText;
    final openingWidth = (door['estimated_opening_width_m'] ?? dims['width_m']);
    final openingHeight =
        (door['estimated_opening_height_m'] ?? dims['height_m']);
    final isDoor = label == '门' || door.isNotEmpty;
    if (isDoor && (openingWidth is num || openingHeight is num)) {
      final width = openingWidth is num ? openingWidth.toStringAsFixed(2) : '—';
      final height = openingHeight is num
          ? openingHeight.toStringAsFixed(2)
          : '—';
      valueText = '门洞净宽 ${width}m · 净高 ${height}m';
    } else {
      final parts = <String>[
        if (fmt('length_m') != null) '长 ${fmt('length_m')}m',
        if (fmt('width_m') != null) '宽 ${fmt('width_m')}m',
        if (fmt('height_m') != null) '高 ${fmt('height_m')}m',
      ];
      valueText = parts.isEmpty ? '无法可靠测量' : parts.join(' · ');
    }

    final subtitleParts = ['测量置信度：$confidence'];
    if (supportingViews is num) {
      subtitleParts.add('支持视角 $supportingViews');
    }
    return ListTile(
      dense: true,
      leading: const Icon(Icons.architecture),
      title: Text('$instanceId · $label'),
      subtitle: Text(subtitleParts.join(' · ')),
      trailing: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 240),
        child: Text(valueText, textAlign: TextAlign.end),
      ),
    );
  }

  List<Widget> _qualityTiles(Report report) {
    final quality = report.measures['reconstruction_quality'] as Map?;
    final training = quality?['training'] as Map?;
    if (training == null || training.isEmpty) {
      return const [
        Padding(padding: EdgeInsets.all(8), child: Text('暂无重建质量指标')),
      ];
    }
    String metric(String key, {int digits = 2}) {
      final value = training[key];
      return value is num ? value.toStringAsFixed(digits) : '未知';
    }

    final selection = training['view_selection'] as Map? ?? const {};
    final timings = training['timings'] as Map? ?? const {};
    final trainViews = selection['training_view_count'] ?? '未知';
    final holdoutViews =
        selection['holdout_view_count'] ??
        training['validation_view_count'] ??
        '未知';
    final seconds = timings['3dgs_seconds'];
    return [
      ListTile(
        dense: true,
        leading: const Icon(Icons.visibility_outlined),
        title: Text('训练视角 $trainViews · 留出视角 $holdoutViews'),
        subtitle: const Text('留出视角未参与训练，用于真实新视角验收'),
      ),
      ListTile(
        dense: true,
        leading: const Icon(Icons.analytics_outlined),
        title: Text(
          'PSNR ${metric('validation_psnr_mean')}（最低 ${metric('validation_psnr_min')}）',
        ),
        subtitle: Text(
          'SSIM ${metric('validation_ssim_mean', digits: 3)}（最低 ${metric('validation_ssim_min', digits: 3)}）',
        ),
      ),
      ListTile(
        dense: true,
        leading: const Icon(Icons.timer_outlined),
        title: Text('实际迭代 ${training['iterations'] ?? '未知'}'),
        subtitle: Text(
          seconds is num ? '重建 ${seconds.toStringAsFixed(1)} 秒' : '重建耗时未知',
        ),
      ),
    ];
  }
}

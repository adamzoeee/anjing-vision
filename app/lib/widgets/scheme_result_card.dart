import 'package:flutter/material.dart';
import '../models/scheme_layout.dart';
import 'scheme_plan_painter.dart';

class SchemeResultCard extends StatelessWidget {
  const SchemeResultCard({
    super.key, required this.payload, this.onSave, this.onEdit, this.onExport,
  });

  final Map<String, dynamic> payload;
  final VoidCallback? onSave;
  final VoidCallback? onEdit;
  final VoidCallback? onExport;

  String _score(Object? value) => value is num ? value.toStringAsFixed(1) : '无法评分';

  static SchemeLayout? _layoutOf(Object? value) =>
      value is Map ? SchemeLayout.fromJson(value.cast<String, dynamic>()) : null;

  @override
  Widget build(BuildContext context) {
    final before = _layoutOf(payload['layout_before']);
    final after = _layoutOf(payload['layout_after']);
    final changed = ((payload['changed_ids'] as List?) ?? const []).map((e) => e.toString()).toSet();
    final removed = ((payload['removed_ids'] as List?) ?? const []).map((e) => e.toString()).toSet();
    final comparison = payload['comparison'] is Map
        ? (payload['comparison'] as Map).cast<String, dynamic>()
        : const <String, dynamic>{};
    final metricChanges = (payload['metric_changes'] as List? ?? const []).whereType<Map>().toList();
    final conflicts = (payload['conflicts'] as List? ?? const []).whereType<Map>().toList();
    // 假设放置提示与叠影图颜色同源：优先看改后布局里的 assumed 物体，
    // 布局缺失时才回退 assumed_ids，避免图与提示不一致。
    final hasAssumed = after != null
        ? after.objects.any((object) => object.assumed)
        : ((payload['assumed_ids'] as List?) ?? const []).isNotEmpty;
    final hasComparison = comparison.isNotEmpty;
    final hasPlan = before != null && after != null;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
            hasComparison
                ? '${_score(comparison['before_score'])} → ${_score(comparison['after_score'])}'
                : '无法评分',
            style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 18)),
        if (hasPlan) ...[
          const SizedBox(height: 8),
          AspectRatio(
            aspectRatio: 1,
            child: CustomPaint(
              painter: SchemePlanPainter(
                before: before, after: after, changedIds: changed, removedIds: removed,
              ),
              child: const SizedBox.expand(),
            ),
          ),
        ] else
          const Padding(
            padding: EdgeInsets.only(top: 8),
            child: Text('缺少改造前后平面数据，暂无法绘制叠影图。'),
          ),
        if (conflicts.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text('该位置与 ${conflicts.first['other']} 重叠，已拒绝',
                style: const TextStyle(color: Colors.red)),
          ),
        if (hasAssumed)
          const Padding(
            padding: EdgeInsets.only(top: 8),
            child: Text('含假设放置物体 · 非实测', style: TextStyle(color: kSchemeAssumed)),
          ),
        const SizedBox(height: 8),
        ...metricChanges.take(6).map((item) => Text(
            '· ${item['name']}：${item['before']}${item['unit'] ?? ''} → ${item['after']}${item['unit'] ?? ''}')),
        const SizedBox(height: 8),
        Wrap(spacing: 8, children: [
          if (onEdit != null) OutlinedButton(onPressed: onEdit, child: const Text('进入沙盒编辑')),
          if (onSave != null) OutlinedButton(onPressed: onSave, child: const Text('保存为方案')),
          if (onExport != null) FilledButton(onPressed: onExport, child: const Text('导出')),
        ]),
        const Padding(
          padding: EdgeInsets.only(top: 8),
          child: Text('沙盒模拟 · 非实测', style: TextStyle(fontSize: 12, color: Colors.grey)),
        ),
      ],
    );
  }
}

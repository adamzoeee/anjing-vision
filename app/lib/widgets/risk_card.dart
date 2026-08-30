import 'package:flutter/material.dart';
import '../api/models.dart';

class RiskCard extends StatelessWidget {
  final Risk risk;
  const RiskCard({super.key, required this.risk});

  @override
  Widget build(BuildContext context) {
    final color = switch (risk.level) {
      'high' || 'red' => Colors.red,
      'medium' || 'yellow' => Colors.orange,
      'low' || 'green' => Colors.green,
      _ => Colors.grey,
    };
    final levelText = switch (risk.level) {
      'high' || 'red' => '高风险',
      'medium' || 'yellow' => '中风险',
      'low' || 'green' => '低风险',
      _ => '无法评估',
    };
    final measurement = risk.measure == null
        ? null
        : '${risk.measure}${risk.unit.isEmpty ? '' : ' ${risk.unit}'}';
    final details = <String>[
      if (measurement != null) '测量值：$measurement',
      if (risk.confidence != null)
        '置信度：${(risk.confidence! * 100).toStringAsFixed(0)}%',
      if (risk.advice != null && risk.level != 'low' && risk.level != 'green')
        '建议：${risk.advice}',
    ];
    final subtitle = risk.assessmentStatus == 'not_evaluable'
        ? '当前数据不足，无法可靠评估该项风险${risk.reason == null ? '' : '（${risk.reason}）'}'
        : details.join('\n');
    return Card(
      child: ListTile(
        leading: Icon(switch (risk.level) {
          'high' || 'red' => Icons.dangerous,
          'medium' || 'yellow' => Icons.warning_amber,
          'low' || 'green' => Icons.check_circle,
          _ => Icons.help_outline,
        }, color: color),
        title: Text(risk.name),
        subtitle: Text(subtitle),
        trailing: Text(levelText,
            style: TextStyle(color: color, fontWeight: FontWeight.bold)),
      ));
  }
}

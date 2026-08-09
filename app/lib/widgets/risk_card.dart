import 'package:flutter/material.dart';
import '../api/models.dart';

class RiskCard extends StatelessWidget {
  final Risk risk;
  const RiskCard({super.key, required this.risk});

  @override
  Widget build(BuildContext context) {
    final color = switch (risk.level) {
      'red' => Colors.red,
      'yellow' => Colors.orange,
      'green' => Colors.green,
      _ => Colors.grey,
    };
    final levelText = switch (risk.level) {
      'red' => '高风险',
      'yellow' => '注意',
      'green' => '正常',
      _ => '未知',
    };
    return Card(
      child: ListTile(
        leading: Icon(switch (risk.level) {
          'red' => Icons.dangerous,
          'yellow' => Icons.warning_amber,
          'green' => Icons.check_circle,
          _ => Icons.help_outline,
        }, color: color),
        title: Text(risk.name),
        subtitle: Text(risk.measure?.toString() ?? ''),
        trailing: Text(levelText,
            style: TextStyle(color: color, fontWeight: FontWeight.bold)),
      ));
  }
}

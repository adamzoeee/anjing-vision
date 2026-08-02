import 'package:flutter/material.dart';

class ScoreGauge extends StatelessWidget {
  final double score;
  const ScoreGauge({super.key, required this.score});

  @override
  Widget build(BuildContext context) {
    final clamped = score.clamp(0, 100);
    final color = clamped >= 80
        ? Colors.green
        : clamped >= 60
            ? Colors.orange
            : Colors.red;
    return Stack(alignment: Alignment.center, children: [
      SizedBox(
        width: 120, height: 120,
        child: CircularProgressIndicator(
          value: clamped / 100,
          strokeWidth: 10,
          color: color,
          backgroundColor: Colors.grey.shade200,
        )),
      Column(mainAxisSize: MainAxisSize.min, children: [
        Text(clamped.toStringAsFixed(1),
            style: TextStyle(fontSize: 28, fontWeight: FontWeight.bold, color: color)),
        const Text('安全评分', style: TextStyle(fontSize: 12, color: Colors.grey)),
      ]),
    ]);
  }
}

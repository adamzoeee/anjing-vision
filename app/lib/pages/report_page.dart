import 'package:flutter/material.dart';
import '../api/models.dart';

class ReportPage extends StatelessWidget {
  final Scan scan;
  const ReportPage({super.key, required this.scan});
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('评估报告')),
      body: Center(child: Text('报告页（B7 实现）scan#${scan.id}')),
    );
  }
}

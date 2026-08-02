import 'package:flutter/material.dart';
import '../api/models.dart';

class CapturePage extends StatelessWidget {
  final Project project;
  final Scan scan;
  const CapturePage({super.key, required this.project, required this.scan});
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('采集：${project.name}')),
      body: Center(child: Text('采集引导页（B5 实现）scan#${scan.id}')),
    );
  }
}

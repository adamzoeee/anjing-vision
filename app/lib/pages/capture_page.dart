import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import '../api/models.dart';
import 'upload_page.dart';

class CapturePage extends StatelessWidget {
  final Project project;
  final Scan scan;
  const CapturePage({super.key, required this.project, required this.scan});

  static const _tips = [
    '1. 找一张 A4 纸，放在地面显眼处（用于尺寸标定）',
    '2. 从门口开始，沿房间边缘慢速走一圈（1~3 分钟）',
    '3. 在角落、门框、卫生间门口停留 2~3 秒',
    '4. 避免逆光拍摄，保证房间光线充足',
    '5. 走完一圈回到门口即可结束',
  ];

  Future<void> _record(BuildContext context) async {
    final picker = ImagePicker();
    final video = await picker.pickVideo(
        source: ImageSource.camera, maxDuration: const Duration(minutes: 5));
    if (video == null) return;
    if (!context.mounted) return;
    Navigator.push(context, MaterialPageRoute(builder: (_) =>
        UploadPage(scan: scan, file: video)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('采集：${project.name}')),
      body: ListView(padding: const EdgeInsets.all(24), children: [
        const Icon(Icons.videocam, size: 64, color: Colors.teal),
        const SizedBox(height: 16),
        const Text('拍摄引导', style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        ..._tips.map((t) => Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Text(t))),
        const SizedBox(height: 24),
        FilledButton.icon(
          onPressed: () => _record(context),
          icon: const Icon(Icons.fiber_manual_record),
          label: const Text('开始录制'),
        ),
        const SizedBox(height: 8),
        const Text('提示：录完回到本页自动进入上传',
            style: TextStyle(fontSize: 12, color: Colors.grey)),
      ]),
    );
  }
}

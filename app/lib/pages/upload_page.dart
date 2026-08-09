import 'dart:async';
import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';
import '../api/client.dart';
import '../api/models.dart';
import 'report_page.dart';

class UploadPage extends StatefulWidget {
  final Scan scan;
  final XFile file;
  const UploadPage({super.key, required this.scan, required this.file});
  @override
  State<UploadPage> createState() => _UploadPageState();
}

class _UploadPageState extends State<UploadPage> {
  Timer? _timer;
  late Scan _scan;
  String? _uploadError;

  @override
  void initState() {
    super.initState();
    _scan = widget.scan;
    _upload();
    _timer = Timer.periodic(const Duration(seconds: 3), (_) => _poll());
  }

  Future<void> _upload() async {
    try {
      final api = context.read<ApiClient>();
      final name = widget.file.name.isEmpty ? 'clip.mp4' : widget.file.name;
      if (kIsWeb) {
        final bytes = await widget.file.readAsBytes();
        await api.uploadVideo(widget.scan.id, bytes, name);
      } else {
        await api.uploadVideoFile(widget.scan.id, widget.file.path, name);
      }
      if (mounted) setState(() => _uploadError = null);
    } catch (e) {
      if (!mounted) return;
      setState(() => _uploadError = '上传失败: $e');
    }
  }

  Future<void> _poll() async {
    try {
      final s = await context.read<ApiClient>().scanStatus(widget.scan.id);
      if (!mounted) return;
      setState(() => _scan = s);
      if (s.done) {
        _timer?.cancel();
        Navigator.pushReplacement(context, MaterialPageRoute(
            builder: (_) => ReportPage(scan: s)));
      } else if (s.failed) {
        _timer?.cancel();
      }
    } catch (_) {
      // 网络抖动忽略，下轮再试
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('评估进度')),
      body: Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
        CircularProgressIndicator(value: _scan.progress / 100),
        const SizedBox(height: 16),
        Text('${_scan.progress}%', style: Theme.of(context).textTheme.headlineSmall),
        const SizedBox(height: 8),
        Text(_scan.message, style: const TextStyle(color: Colors.grey)),
        if (_uploadError != null) ...[
          const SizedBox(height: 12),
          Text(_uploadError!, style: const TextStyle(color: Colors.red)),
          TextButton(onPressed: _upload, child: const Text('重试上传')),
        ],
        if (_scan.failed) ...[
          const SizedBox(height: 12),
          const Text('评估失败', style: TextStyle(color: Colors.red)),
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('返回')),
        ],
      ])),
    );
  }
}

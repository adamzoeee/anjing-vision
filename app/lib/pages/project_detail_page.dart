import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../api/client.dart';
import '../api/models.dart';
import 'capture_page.dart';
import 'compare_page.dart';
import 'report_page.dart';

class ProjectDetailPage extends StatefulWidget {
  final Project project;
  const ProjectDetailPage({super.key, required this.project});
  @override
  State<ProjectDetailPage> createState() => _ProjectDetailPageState();
}

class _ProjectDetailPageState extends State<ProjectDetailPage> {
  List<Scan>? _scans;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final list = await context.read<ApiClient>().listScans(widget.project.id);
      if (!mounted) return;
      setState(() {
        _scans = list;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = '$e');
    }
  }

  Future<void> _startCapture() async {
    final scan = await context.read<ApiClient>()
        .createScan(widget.project.id, 'video');
    if (!mounted) return;
    Navigator.push(context, MaterialPageRoute(
        builder: (_) => CapturePage(project: widget.project, scan: scan)));
  }

  void _compare() {
    final scans = _scans;
    if (scans == null) return;
    final done = scans.where((s) => s.done).toList()
      ..sort((a, b) => b.id.compareTo(a.id));
    if (done.length < 2) return;
    // 最近两次完成的扫描：旧→新
    Navigator.push(context, MaterialPageRoute(builder: (_) =>
        ComparePage(scanA: done[1].id, scanB: done[0].id)));
  }

  @override
  Widget build(BuildContext context) {
    final scans = _scans;
    return Scaffold(
      appBar: AppBar(title: Text(widget.project.name)),
      floatingActionButton: FloatingActionButton(
        onPressed: _startCapture, child: const Icon(Icons.videocam)),
      body: _error != null
          ? Center(child: Text(_error!))
          : scans == null
              ? const Center(child: CircularProgressIndicator())
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(children: [
                    Padding(
                      padding: const EdgeInsets.all(16),
                      child: Text('扫描记录（${scans.length}）',
                          style: Theme.of(context).textTheme.titleMedium)),
                    if (scans.isEmpty)
                      const Padding(
                        padding: EdgeInsets.all(16),
                        child: Text('暂无扫描记录，点击右下角开始采集'))
                    else
                      ...scans.map((s) => ListTile(
                        title: Text('#${s.id}  ${s.status}'),
                        subtitle: Text('${s.progress}% ${s.message}'),
                        trailing: s.done
                            ? const Icon(Icons.description, color: Colors.teal)
                            : s.failed
                                ? const Icon(Icons.error_outline, color: Colors.red)
                                : const Icon(Icons.hourglass_empty),
                        onTap: s.done
                            ? () => Navigator.push(context, MaterialPageRoute(
                                builder: (_) => ReportPage(scan: s)))
                            : null,
                      )),
                    const SizedBox(height: 24),
                    Center(child: OutlinedButton.icon(
                      onPressed: _scans != null && _scans!.where((s) => s.done).length >= 2
                          ? _compare
                          : null,
                      icon: const Icon(Icons.compare_arrows),
                      label: const Text('改造前后对比'),
                    )),
                  ])),
    );
  }
}

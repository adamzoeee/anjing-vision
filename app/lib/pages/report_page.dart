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
                  Center(child: ScoreGauge(score: r.score)),
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
                  Text(
                    '风险项（${r.risks.length}）',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  if (r.risks.isEmpty)
                    const Padding(
                      padding: EdgeInsets.all(8),
                      child: Text('未检测到风险项'),
                    )
                  else
                    ...r.risks.map((risk) => RiskCard(risk: risk)),
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
                    child: r.previewPly == null
                        ? const Text(
                            '暂无 3D 预览',
                            style: TextStyle(color: Colors.white54),
                          )
                        : ElevatedButton.icon(
                            onPressed: () {
                              final gaussianPly = r.previewGaussianPly;
                              final cameras = r.previewCameras;
                              final model = Uri.encodeComponent(
                                '/static/${r.scanId}/preview/'
                                '${gaussianPly ?? r.previewPly}',
                              );
                              final token = Uri.encodeComponent(
                                api.token ?? '',
                              );
                              final cameraQuery = cameras == null
                                  ? ''
                                  : '&cameras=${Uri.encodeComponent('/static/${r.scanId}/preview/$cameras')}';
                              final previewUrl = gaussianPly == null
                                  ? '${api.dio.options.baseUrl}/preview/?ply=$model&token=$token'
                                  : '${api.dio.options.baseUrl}/preview/gaussian.html?url=$model$cameraQuery#token=$token';
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
}

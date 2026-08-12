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
                  Text('尺寸信息', style: Theme.of(context).textTheme.titleMedium),
                  ..._measurementTiles(r),
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

  List<Widget> _measurementTiles(Report report) {
    final raw = report.measures['reference_measurements'] as List? ?? const [];
    final calibrationRaw = report.measures['calibration_quality'];
    final calibration = calibrationRaw is Map ? calibrationRaw : const {};
    final calibrationReferences = calibration['references'] is List
        ? calibration['references'] as List
        : const [];
    final labels = {
      'bed': '床',
      'table': '桌子',
      'door': '门',
      'sofa': '沙发',
      'cabinet': '柜子',
    };
    final dimensions = {'length': '长', 'width': '宽', 'height': '高'};
    final calibrationSucceeded = report.calibrated == 3;
    final usedCount = calibration['used_count'] is num
        ? (calibration['used_count'] as num).toInt()
        : 0;
    final failureReason = calibration['reason']?.toString();
    final tiles = <Widget>[
      ListTile(
        dense: true,
        leading: Icon(
          calibrationSucceeded ? Icons.check_circle : Icons.info_outline,
          color: calibrationSucceeded ? Colors.green : Colors.orange,
        ),
        title: Text(calibrationSucceeded ? '尺度标定：成功' : '尺度标定：失败'),
        subtitle: Text(
          calibrationSucceeded
              ? '已使用 $usedCount 个参考尺寸恢复米制比例'
              : (failureReason ?? '未能用至少两个一致且成功识别的参考尺寸恢复米制比例'),
        ),
      ),
    ];
    for (final item in raw.whereType<Map>()) {
      final object = item['object_type']?.toString();
      final dimension = item['dimension']?.toString();
      final meters = item['meters'];
      if (object == null || dimension == null || meters == null) continue;
      Map? detail;
      for (final candidate in calibrationReferences.whereType<Map>()) {
        if (candidate['object_type']?.toString() == object &&
            candidate['dimension']?.toString() == dimension) {
          detail = candidate;
          break;
        }
      }
      final status = detail?['status']?.toString();
      final statusText = switch (status) {
        'used' => '已识别并用于尺度标定',
        'not_detected' => '未在重建结果中识别到对应参考物',
        'outlier' => '已识别，但推导比例与其他参考不一致，未采用',
        _ => calibrationSucceeded ? '用户输入参考值' : '用户输入参考值（未用于米制标定）',
      };
      tiles.add(
        ListTile(
          dense: true,
          leading: const Icon(Icons.straighten),
          title: Text(
            '${labels[object] ?? object}${dimensions[dimension] ?? dimension}',
          ),
          trailing: Text('${(meters as num).toStringAsFixed(2)} m'),
          subtitle: Text(statusText),
        ),
      );
    }
    if (raw.isEmpty) {
      tiles.add(
        const Padding(padding: EdgeInsets.all(8), child: Text('未记录用户参考尺寸')),
      );
    }
    final extent = report.measures['reconstruction_extent_m'] as List?;
    if (extent != null && extent.length >= 3 && report.calibrated == 3) {
      tiles.add(
        ListTile(
          dense: true,
          leading: const Icon(Icons.view_in_ar),
          title: const Text('重建空间包围尺寸（长 × 宽 × 高）'),
          trailing: Text(
            extent
                .take(3)
                .map((v) => '${(v as num).toStringAsFixed(2)}m')
                .join(' × '),
          ),
        ),
      );
    }
    return tiles;
  }
}

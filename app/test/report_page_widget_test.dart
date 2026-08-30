import 'package:anjing_app/api/client.dart';
import 'package:anjing_app/api/models.dart';
import 'package:anjing_app/pages/report_page.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http_mock_adapter/http_mock_adapter.dart';
import 'package:provider/provider.dart';

void main() {
  late Dio dio;
  late DioAdapter adapter;
  late ApiClient api;
  late Scan scan;

  setUp(() {
    dio = Dio(
      BaseOptions(
        baseUrl: 'https://api.test.invalid',
        contentType: Headers.jsonContentType,
      ),
    );
    api = ApiClient(dio: dio);
    adapter = DioAdapter(
      dio: dio,
      matcher: const FullHttpRequestMatcher(needsExactBody: true),
    );
    scan = Scan(
      id: 12,
      projectId: 3,
      status: 'done',
      progress: 100,
      message: '已完成',
      captureType: 'video',
    );
  });

  Widget reportApp() {
    return Provider<ApiClient>.value(
      value: api,
      child: MaterialApp(home: ReportPage(scan: scan)),
    );
  }

  testWidgets('报告加载时显示进度指示器', (tester) async {
    adapter.onGet(
      '/api/reports/scans/12',
      (server) => server.reply(200, {
        'scan_id': 12,
        'score': 90,
        'risks': [],
        'advice': [],
        'images': [],
        'calibrated': 1,
      }, delay: const Duration(seconds: 1)),
    );

    await tester.pumpWidget(reportApp());
    await tester.pump();

    expect(find.byType(CircularProgressIndicator), findsOneWidget);

    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();
    expect(find.text('90.0'), findsOneWidget);
  });

  testWidgets('报告接口错误显示错误状态', (tester) async {
    adapter.onGet(
      '/api/reports/scans/12',
      (server) => server.reply(500, {'detail': '报告生成失败'}),
    );

    await tester.pumpWidget(reportApp());
    await tester.pumpAndSettle();

    expect(find.textContaining('500'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });

  testWidgets('显示安全评分、风险项和改造建议', (tester) async {
    adapter.onGet(
      '/api/reports/scans/12',
      (server) => server.reply(200, {
        'scan_id': 12,
        'score': 78.5,
        'risks': [
          {
            'code': 'door_width',
            'name': '门宽不足',
            'level': 'red',
            'measure': 0.72,
          },
          {
            'code': 'floor_obstacle',
            'name': '地面障碍物',
            'level': 'yellow',
            'measure': '通道有杂物',
          },
        ],
        'advice': ['建议拓宽门洞', '清理通道杂物'],
        'images': [],
        'calibrated': 1,
      }),
    );

    await tester.pumpWidget(reportApp());
    await tester.pumpAndSettle();

    expect(find.text('78.5'), findsOneWidget);
    expect(find.text('安全评分'), findsOneWidget);
    expect(find.text('已按 A4 纸标定真实尺寸'), findsOneWidget);
    expect(find.text('风险项（2）'), findsOneWidget);
    expect(find.text('门宽不足'), findsOneWidget);
    expect(find.text('高风险'), findsOneWidget);
    expect(find.text('地面障碍物'), findsOneWidget);
    expect(find.text('注意'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('建议拓宽门洞'),
      300,
      scrollable: find.byType(Scrollable),
    );
    expect(find.text('建议拓宽门洞'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('清理通道杂物'),
      200,
      scrollable: find.byType(Scrollable),
    );
    expect(find.text('清理通道杂物'), findsOneWidget);
  });

  testWidgets('无风险和无建议时显示明确空状态', (tester) async {
    adapter.onGet(
      '/api/reports/scans/12',
      (server) => server.reply(200, {
        'scan_id': 12,
        'score': 96,
        'risks': [],
        'advice': [],
        'images': [],
        'calibrated': 0,
      }),
    );

    await tester.pumpWidget(reportApp());
    await tester.pumpAndSettle();

    expect(find.text('96.0'), findsOneWidget);
    expect(find.text('风险项（0）'), findsOneWidget);
    expect(find.text('未检测到风险项'), findsOneWidget);
    expect(find.text('无需改造建议'), findsOneWidget);
    expect(find.textContaining('未完成尺寸标定'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('暂无标注图'),
      300,
      scrollable: find.byType(Scrollable),
    );
    expect(find.text('暂无标注图'), findsOneWidget);
  });

  testWidgets('正式评估显示覆盖率分类分数Top风险和不可评估项', (tester) async {
    final highRisk = {
      'risk_code': 'door_width_high',
      'risk_type': 'mobility',
      'risk_name': '门净宽风险',
      'metric_code': 'door_width',
      'measured_value': 0.75,
      'unit': 'm',
      'threshold': {},
      'position': {'object_id': 'door_01'},
      'risk_level': 'high',
      'confidence': 0.9,
      'reason': 'threshold',
      'advice': '评估扩宽门洞。',
      'assessment_status': 'evaluated',
      'related_object_ids': ['door_01'],
      'related_path_id': null,
    };
    final unknownRisk = {
      'risk_code': 'activity_area_not_evaluable',
      'risk_type': 'layout',
      'risk_name': '活动区域面积风险',
      'metric_code': 'activity_area',
      'measured_value': null,
      'unit': 'm²',
      'threshold': {},
      'position': null,
      'risk_level': null,
      'confidence': null,
      'reason': 'explicit_activity_anchor_missing',
      'advice': null,
      'assessment_status': 'not_evaluable',
      'related_object_ids': [],
      'related_path_id': null,
    };
    adapter.onGet(
      '/api/reports/scans/12',
      (server) => server.reply(200, {
        'scan_id': 12,
        'score': 72.5,
        'risks': [highRisk, unknownRisk],
        'advice': ['评估扩宽门洞。'],
        'images': [],
        'calibrated': 3,
        'measures': {
          'risk_assessment': {
            'official': true,
            'overall': {
              'status': 'evaluated',
              'score': 72.5,
              'confidence': 0.82,
              'coverage_percent': 86.7,
            },
            'category_scores': {
              'mobility': {'score': 68.0, 'weight': 0.4},
              'layout': {'score': 75.0, 'weight': 0.3},
              'usage_safety': {'score': 76.0, 'weight': 0.3},
            },
            'top_risks': [highRisk],
            'not_evaluable': [unknownRisk],
          },
        },
      }),
    );
    await tester.pumpWidget(reportApp());
    await tester.pumpAndSettle();
    expect(find.text('正式空间风险评估'), findsOneWidget);
    expect(find.text('评估覆盖率：86.7%'), findsOneWidget);
    expect(find.text('综合置信度：82.0%'), findsOneWidget);
    expect(find.text('通行能力（40.0%）'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('风险项（1）'), 300, scrollable: find.byType(Scrollable),
    );
    expect(find.text('门净宽风险'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('不可评估项（1）'), 300, scrollable: find.byType(Scrollable),
    );
    expect(find.text('活动区域面积风险'), findsOneWidget);
  });

  testWidgets('未知风险状态使用帮助图标而不是正常图标', (tester) async {
    adapter.onGet(
      '/api/reports/scans/12',
      (server) => server.reply(200, {
        'scan_id': 12,
        'score': 84,
        'risks': [
          {
            'code': 'obstacle',
            'name': '通道障碍物',
            'level': 'unknown',
            'measure': null,
          },
        ],
        'advice': [],
        'images': [],
        'calibrated': 1,
      }),
    );

    await tester.pumpWidget(reportApp());
    await tester.pumpAndSettle();

    expect(find.text('未知'), findsOneWidget);
    expect(find.byIcon(Icons.help_outline), findsOneWidget);
    expect(find.byIcon(Icons.check_circle), findsNothing);
  });

  testWidgets('显示自动尺寸和未参与训练的重建质量指标', (tester) async {
    adapter.onGet(
      '/api/reports/scans/12',
      (server) => server.reply(200, {
        'scan_id': 12,
        'score': 88,
        'risks': [],
        'advice': [],
        'images': [],
        'calibrated': 3,
        'measures': {
          'room_dimensions': {
            'status': 'measured',
            'confidence': 'high',
            'dimensions': {'length': 4.2, 'width': 3.1, 'height': 2.6},
          },
          'object_dimensions': {
            '床': {
              'status': 'measured',
              'confidence': 'medium',
              'dimensions': {'length': 2.0, 'width': 1.51, 'height': 0.48},
            },
          },
          'reconstruction_quality': {
            'training': {
              'validation_psnr_mean': 24.51,
              'validation_psnr_min': 19.02,
              'validation_ssim_mean': 0.812,
              'validation_ssim_min': 0.701,
              'iterations': 8000,
              'validation_view_count': 12,
              'view_selection': {
                'training_view_count': 80,
                'holdout_view_count': 12,
              },
              'timings': {'3dgs_seconds': 612.4},
            },
          },
        },
      }),
    );

    await tester.pumpWidget(reportApp());
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('重建质量'),
      300,
      scrollable: find.byType(Scrollable),
    );

    expect(find.text('房间自动测量'), findsOneWidget);
    expect(find.text('床自动测量'), findsOneWidget);
    expect(find.text('重建质量'), findsOneWidget);
    expect(find.textContaining('训练视角 80'), findsOneWidget);
    expect(find.textContaining('PSNR 24.51'), findsOneWidget);
    expect(find.textContaining('SSIM 0.812'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.textContaining('实际迭代 8000'),
      200,
      scrollable: find.byType(Scrollable),
    );
    expect(find.textContaining('实际迭代 8000'), findsOneWidget);
  });

  test('标注图片使用后端资源地址并携带认证请求头', () {
    api.setToken('image-token');
    final provider = authenticatedReportImage(api, '/static/12/view_0.png');
    expect(provider.url, 'https://api.test.invalid/static/12/view_0.png');
    expect(provider.headers, {'Authorization': 'Bearer image-token'});
  });

  testWidgets('标定成功时显示参考尺寸和重建空间尺寸', (tester) async {
    adapter.onGet(
      '/api/reports/scans/12',
      (server) => server.reply(200, {
        'scan_id': 12,
        'score': 90,
        'risks': [],
        'advice': [],
        'images': [],
        'calibrated': 3,
        'measures': {
          'reference_measurements': [
            {'object_type': 'door', 'dimension': 'height', 'meters': 2.0},
            {'object_type': 'door', 'dimension': 'width', 'meters': 0.9},
          ],
          'calibration_quality': {
            'used_count': 2,
            'references': [
              {
                'object_type': 'door',
                'dimension': 'height',
                'meters': 2.0,
                'status': 'used',
              },
              {
                'object_type': 'door',
                'dimension': 'width',
                'meters': 0.9,
                'status': 'used',
              },
            ],
          },
          'reconstruction_extent_m': [4.2, 3.1, 2.6],
        },
      }),
    );

    await tester.pumpWidget(reportApp());
    await tester.pumpAndSettle();
    expect(find.text('尺度标定：成功'), findsOneWidget);
    expect(find.text('2.00 m'), findsOneWidget);
    expect(find.text('0.90 m'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('4.20m × 3.10m × 2.60m'),
      250,
      scrollable: find.byType(Scrollable),
    );
    expect(find.text('4.20m × 3.10m × 2.60m'), findsOneWidget);
  });

  testWidgets('标定失败时仍显示输入值和失败原因', (tester) async {
    adapter.onGet(
      '/api/reports/scans/12',
      (server) => server.reply(200, {
        'scan_id': 12,
        'score': 70,
        'risks': [],
        'advice': [],
        'images': [],
        'calibrated': 0,
        'measures': {
          'reference_measurements': [
            {'object_type': 'bed', 'dimension': 'length', 'meters': 2.0},
          ],
          'calibration_quality': {
            'used_count': 0,
            'reason': '至少需要两个被模型成功识别的参考尺寸',
            'references': [
              {
                'object_type': 'bed',
                'dimension': 'length',
                'meters': 2.0,
                'status': 'not_detected',
              },
            ],
          },
        },
      }),
    );

    await tester.pumpWidget(reportApp());
    await tester.pumpAndSettle();
    expect(find.text('尺度标定：失败'), findsOneWidget);
    expect(find.text('至少需要两个被模型成功识别的参考尺寸'), findsOneWidget);
    expect(find.text('2.00 m'), findsOneWidget);
    expect(find.text('未在重建结果中识别到对应参考物'), findsOneWidget);
  });

  testWidgets('第二阶段语义空间：显示实例卡片与门洞宽高', (tester) async {
    adapter.onGet(
      '/api/reports/scans/12',
      (server) => server.reply(200, {
        'scan_id': 12,
        'score': 90,
        'risks': [],
        'advice': [],
        'images': [],
        'calibrated': 4,
        'measures': {
          'room_dimensions': {
            'status': 'measured',
            'confidence': 'high',
            'dimensions': {'length': 4.82, 'width': 3.61, 'height': 2.74},
          },
          'semantic_space': {
            'unit': 'meters',
            'metric_available': true,
            'objects': [
              {
                'instance_id': 'bed_01',
                'label': '床',
                'status': 'measured',
                'dimensions': {
                  'length_m': 2.03,
                  'width_m': 1.51,
                  'height_m': 0.48,
                },
                'measurement_confidence': 'high',
                'supporting_views': 5,
                'metadata': {},
              },
              {
                'instance_id': 'door_01',
                'label': '门',
                'status': 'measured',
                'dimensions': {
                  'length_m': null,
                  'width_m': 0.86,
                  'height_m': 2.04,
                },
                'measurement_confidence': 'medium',
                'supporting_views': 4,
                'metadata': {
                  'door_measurement': {
                    'method': 'door_jamb_columns',
                    'estimated_opening_width_m': 0.86,
                    'estimated_opening_height_m': 2.04,
                  },
                },
              },
              {
                'instance_id': 'cabinet_01',
                'label': '柜子',
                'status': 'unknown',
                'reason': 'too_few_points',
                'dimensions': {
                  'length_m': null,
                  'width_m': null,
                  'height_m': null,
                },
                'measurement_confidence': 'low',
                'supporting_views': 1,
                'metadata': {},
              },
            ],
          },
        },
      }),
    );

    await tester.pumpWidget(reportApp());
    await tester.pumpAndSettle();

    expect(find.text('bed_01 · 床'), findsOneWidget);
    expect(find.textContaining('长 2.03m'), findsOneWidget);
    expect(find.textContaining('宽 1.51m'), findsOneWidget);
    expect(find.textContaining('测量置信度：高'), findsOneWidget);
    expect(find.textContaining('支持视角 5'), findsOneWidget);
    expect(find.text('door_01 · 门'), findsOneWidget);
    expect(find.textContaining('门洞净宽 0.86m'), findsOneWidget);
    expect(find.textContaining('净高 2.04m'), findsOneWidget);
    // 未可靠测量的实例不显示尺寸卡片
    expect(find.textContaining('cabinet_01'), findsNothing);
    // 有语义空间数据时不回退旧 object_dimensions 逻辑
    expect(find.text('床自动测量'), findsNothing);
  });
}

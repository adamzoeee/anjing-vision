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
    expect(find.textContaining('实际迭代 8000'), findsOneWidget);
  });

  test('标注图片使用后端资源地址并携带认证请求头', () {
    api.setToken('image-token');
    final provider = authenticatedReportImage(api, '/static/12/view_0.png');
    expect(provider.url, 'https://api.test.invalid/static/12/view_0.png');
    expect(provider.headers, {'Authorization': 'Bearer image-token'});
  });
}

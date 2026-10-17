import 'dart:typed_data';

import 'package:anjing_app/api/client.dart';
import 'package:anjing_app/api/live_capture.dart';
import 'package:anjing_app/api/models.dart';
import 'package:anjing_app/pages/live_capture_page.dart';
import 'package:anjing_app/state/auth_store.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';

class CaptureApi extends ApiClient {
  bool enabled = true;
  String state = 'stopped';
  String? error;
  int starts = 0, stops = 0, frames = 0, statuses = 0;
  bool failStatus = false;
  CancelToken? latestToken;

  LiveCaptureStatus snapshot() => LiveCaptureStatus.fromJson({
    'enabled': enabled,
    'state': state,
    'source': {'type': 'file'},
    'preview_enabled': true,
    'thread_alive': state == 'running',
    'last_error': error == null ? null : {'message': error},
    'input_fps': state == 'running' ? 10 : 0,
  });
  @override
  Future<LiveCaptureStatus> liveCaptureStatus({
    CancelToken? cancelToken,
  }) async {
    latestToken = cancelToken;
    statuses++;
    if (failStatus) throw StateError('offline');
    return snapshot();
  }

  @override
  Future<LiveCaptureStatus> startLiveCapture({CancelToken? cancelToken}) async {
    starts++;
    state = 'running';
    return snapshot();
  }

  @override
  Future<LiveCaptureStatus> stopLiveCapture({CancelToken? cancelToken}) async {
    stops++;
    state = 'stopped';
    return snapshot();
  }

  @override
  Future<Uint8List?> liveCaptureFrame({CancelToken? cancelToken}) async {
    latestToken = cancelToken;
    frames++;
    return null; // 204: no image yet, not a decoding error.
  }
}

Future<void> showPage(
  WidgetTester tester,
  CaptureApi api, {
  bool admin = true,
}) async {
  final store = AuthStore(api)
    ..user = AuthUser(
      id: 1,
      name: 'Test',
      email: 't@example.com',
      role: admin ? 'admin' : 'member',
      orgName: 'Test',
    );
  await tester.pumpWidget(
    MultiProvider(
      providers: [
        Provider<ApiClient>.value(value: api),
        ChangeNotifierProvider<AuthStore>.value(value: store),
      ],
      child: const MaterialApp(home: LiveCapturePage()),
    ),
  );
  await tester.pump();
}

void main() {
  testWidgets('默认禁用时不发预览请求且不能启动', (tester) async {
    final api = CaptureApi()..enabled = false;
    await showPage(tester, api);
    expect(find.textContaining('实时采集未启用'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const Key('capture-start')))
          .onPressed,
      isNull,
    );
    await tester.pump(const Duration(seconds: 1));
    expect(api.frames, 0);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('启停与无画面降级，离开页面只取消请求', (tester) async {
    final api = CaptureApi();
    await showPage(tester, api);
    await tester.tap(find.byKey(const Key('capture-start')));
    await tester.pump();
    expect(api.starts, 1);
    await tester.pump(const Duration(milliseconds: 300));
    expect(api.frames, greaterThan(0));
    expect(find.text('暂无预览画面'), findsOneWidget);
    expect(find.textContaining('下游消费 FPS：—'), findsOneWidget);
    await tester.tap(find.byKey(const Key('capture-stop')));
    await tester.pump();
    expect(api.stops, 1);
    final token = api.latestToken!;
    final count = api.statuses;
    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(seconds: 2));
    expect(token.isCancelled, true);
    expect(api.statuses, count);
    expect(api.stops, 1);
  });

  testWidgets('错误状态可见，同机构普通用户无控制按钮', (tester) async {
    final api = CaptureApi()
      ..state = 'error'
      ..error = '摄像头无法打开';
    await showPage(tester, api, admin: false);
    expect(find.text('状态：采集错误'), findsOneWidget);
    expect(find.textContaining('摄像头无法打开'), findsOneWidget);
    expect(find.byKey(const Key('capture-start')), findsNothing);
    expect(api.frames, 0);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('后端不可达正常降级', (tester) async {
    final api = CaptureApi()..failStatus = true;
    await showPage(tester, api);
    expect(find.byKey(const Key('capture-api-error')), findsOneWidget);
    expect(find.textContaining('无法连接实时采集服务'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('关闭预览与后台切换不停止共享采集', (tester) async {
    final api = CaptureApi()..state = 'running';
    await showPage(tester, api);
    await tester.pump(const Duration(milliseconds: 300));
    final first = api.frames;
    await tester.tap(find.byType(SwitchListTile));
    await tester.pump(const Duration(seconds: 1));
    expect(api.frames, first);
    expect(api.stops, 0);
    final previousToken = api.latestToken!;
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    await tester.pump(const Duration(seconds: 2));
    expect(previousToken.isCancelled, true);
    final count = api.statuses;
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pump();
    expect(api.statuses, greaterThan(count));
    expect(api.stops, 0);
    await tester.pumpWidget(const SizedBox());
  });
}

import 'dart:async';

import 'package:anjing_app/api/client.dart';
import 'package:anjing_app/api/models.dart';
import 'package:anjing_app/main.dart';
import 'package:anjing_app/pages/login_page.dart';
import 'package:anjing_app/pages/projects_page.dart';
import 'package:anjing_app/state/auth_store.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http_mock_adapter/http_mock_adapter.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

class DeferredTokenStorage implements TokenStorage {
  DeferredTokenStorage({this.token});

  String? token;
  final readCompleter = Completer<String?>();
  final clearCompleter = Completer<void>();
  bool clearCalled = false;

  @override
  Future<void> clear() {
    clearCalled = true;
    token = null;
    return clearCompleter.future;
  }

  @override
  Future<String?> read() => readCompleter.future;

  @override
  Future<void> write(String value) async {
    token = value;
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const userJson = {
    'id': 7,
    'name': '李阿姨',
    'email': 'li@example.com',
    'role': 'member',
    'org_name': '安心养老',
  };

  late Dio dio;
  late DioAdapter adapter;
  late ApiClient api;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
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
  });

  Widget gateApp(AuthStore store) {
    return MultiProvider(
      providers: [
        Provider<ApiClient>.value(value: api),
        ChangeNotifierProvider<AuthStore>.value(value: store),
      ],
      child: const MaterialApp(home: SessionGate()),
    );
  }

  testWidgets('启动恢复未完成时显示明确的恢复状态', (tester) async {
    final storage = DeferredTokenStorage();
    final store = AuthStore(api, tokenStorage: storage);
    final restoreFuture = store.restore();

    await tester.pumpWidget(gateApp(store));

    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    expect(find.text('正在恢复登录状态…'), findsOneWidget);

    storage.readCompleter.complete(null);
    await restoreFuture;
    await tester.pump();

    expect(find.byType(LoginPage), findsOneWidget);
  });

  testWidgets('有效 Token 恢复后直接进入项目页', (tester) async {
    SharedPreferences.setMockInitialValues({'token': 'valid-token'});
    final store = AuthStore(api);
    adapter.onGet('/api/auth/me', (server) => server.reply(200, userJson));
    adapter.onGet(
      '/api/projects',
      (server) => server.reply(200, <Map<String, dynamic>>[]),
    );

    await tester.pumpWidget(gateApp(store));
    expect(find.text('正在恢复登录状态…'), findsOneWidget);

    await tester.runAsync(store.restore);
    await tester.pumpAndSettle();

    expect(find.byType(ProjectsPage), findsOneWidget);
    expect(find.text('项目列表（安心养老）'), findsOneWidget);
    expect(find.byType(LoginPage), findsNothing);
  });

  testWidgets('正式 SessionGate 退出再登录只保留一个页面且无多余路由', (tester) async {
    final storage = DeferredTokenStorage(token: 'old-token');
    final store = AuthStore(api, tokenStorage: storage)
      ..user = AuthUser.fromJson(userJson)
      ..status = AuthStatus.authenticated;
    adapter.onGet(
      '/api/projects',
      (server) => server.reply(200, <Map<String, dynamic>>[]),
    );

    await tester.pumpWidget(gateApp(store));
    await tester.pumpAndSettle();

    expect(find.byType(ProjectsPage), findsOneWidget);
    expect(find.byType(LoginPage), findsNothing);

    await tester.tap(find.byIcon(Icons.logout));
    await tester.pump();

    expect(storage.clearCalled, isTrue);
    expect(find.byType(ProjectsPage), findsOneWidget);
    expect(find.byType(LoginPage), findsNothing);

    storage.clearCompleter.complete();
    await tester.pumpAndSettle();

    expect(find.byType(LoginPage), findsOneWidget);
    expect(find.byType(ProjectsPage), findsNothing);

    adapter.onPost(
      '/api/auth/login',
      (server) => server.reply(200, {'token': 'new-token', 'user': userJson}),
      data: {'email': 'li@example.com', 'password': 'secret123'},
    );
    adapter.onGet(
      '/api/projects',
      (server) => server.reply(200, <Map<String, dynamic>>[]),
    );
    await tester.enterText(
      find.widgetWithText(TextField, '邮箱'),
      'li@example.com',
    );
    await tester.enterText(find.widgetWithText(TextField, '密码'), 'secret123');
    await tester.tap(find.widgetWithText(ElevatedButton, '登录'));
    await tester.pumpAndSettle();

    expect(find.byType(ProjectsPage), findsOneWidget);
    expect(find.byType(LoginPage), findsNothing);
    final navigator = tester.state<NavigatorState>(find.byType(Navigator));
    expect(navigator.canPop(), isFalse);
  });
}

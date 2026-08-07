import 'package:anjing_app/api/client.dart';
import 'package:anjing_app/api/models.dart';
import 'package:anjing_app/pages/login_page.dart';
import 'package:anjing_app/pages/projects_page.dart';
import 'package:anjing_app/state/auth_store.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http_mock_adapter/http_mock_adapter.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
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
  late AuthStore authStore;

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
    authStore = AuthStore(api);
  });

  Widget loginApp() {
    return MultiProvider(
      providers: [
        Provider<ApiClient>.value(value: api),
        ChangeNotifierProvider<AuthStore>.value(value: authStore),
      ],
      child: MaterialApp(
        home: const LoginPage(),
        routes: {
          '/projects': (_) =>
              const Scaffold(body: Center(child: Text('项目页已打开'))),
        },
      ),
    );
  }

  Widget projectsApp() {
    authStore.user = AuthUser.fromJson(userJson);
    return MultiProvider(
      providers: [
        Provider<ApiClient>.value(value: api),
        ChangeNotifierProvider<AuthStore>.value(value: authStore),
      ],
      child: MaterialApp(
        home: const ProjectsPage(),
        routes: {
          '/login': (_) => const Scaffold(body: Center(child: Text('登录页已打开'))),
        },
      ),
    );
  }

  group('LoginPage', () {
    testWidgets('输入邮箱密码并点击登录后进入项目页', (tester) async {
      adapter.onPost(
        '/api/auth/login',
        (server) =>
            server.reply(200, {'token': 'widget-token', 'user': userJson}),
        data: {'email': 'li@example.com', 'password': 'secret123'},
      );
      await tester.pumpWidget(loginApp());

      await tester.enterText(
        find.widgetWithText(TextField, '邮箱'),
        'li@example.com',
      );
      await tester.enterText(find.widgetWithText(TextField, '密码'), 'secret123');
      await tester.tap(find.widgetWithText(ElevatedButton, '登录'));
      await tester.pumpAndSettle();

      expect(find.text('项目页已打开'), findsOneWidget);
      final preferences = await SharedPreferences.getInstance();
      expect(preferences.getString('token'), 'widget-token');
    });

    testWidgets('登录请求进行中禁用登录按钮', (tester) async {
      adapter.onPost(
        '/api/auth/login',
        (server) => server.reply(200, {
          'token': 'widget-token',
          'user': userJson,
        }, delay: const Duration(seconds: 1)),
        data: {'email': 'li@example.com', 'password': 'secret123'},
      );
      await tester.pumpWidget(loginApp());
      await tester.enterText(
        find.widgetWithText(TextField, '邮箱'),
        'li@example.com',
      );
      await tester.enterText(find.widgetWithText(TextField, '密码'), 'secret123');

      await tester.tap(find.widgetWithText(ElevatedButton, '登录'));
      await tester.pump();

      final button = tester.widget<ElevatedButton>(
        find.widgetWithText(ElevatedButton, '登录'),
      );
      expect(button.onPressed, isNull);

      await tester.pump(const Duration(seconds: 1));
      await tester.pumpAndSettle();
      expect(find.text('项目页已打开'), findsOneWidget);
    });

    testWidgets('登录失败时显示错误且停留在登录页', (tester) async {
      adapter.onPost(
        '/api/auth/login',
        (server) => server.reply(401, {'detail': 'Invalid token'}),
        data: {'email': 'li@example.com', 'password': 'wrong-password'},
      );
      await tester.pumpWidget(loginApp());
      await tester.enterText(
        find.widgetWithText(TextField, '邮箱'),
        'li@example.com',
      );
      await tester.enterText(
        find.widgetWithText(TextField, '密码'),
        'wrong-password',
      );

      await tester.tap(find.widgetWithText(ElevatedButton, '登录'));
      await tester.pumpAndSettle();

      expect(find.textContaining('登录失败'), findsOneWidget);
      expect(find.byType(LoginPage), findsOneWidget);
      expect(find.text('项目页已打开'), findsNothing);
    });
  });

  group('ProjectsPage', () {
    testWidgets('加载项目时显示进度指示器', (tester) async {
      adapter.onGet(
        '/api/projects',
        (server) => server.reply(
          200,
          <Map<String, dynamic>>[],
          delay: const Duration(seconds: 1),
        ),
      );

      await tester.pumpWidget(projectsApp());
      await tester.pump();

      expect(find.byType(CircularProgressIndicator), findsOneWidget);

      await tester.pump(const Duration(seconds: 1));
      await tester.pumpAndSettle();
      expect(find.text('暂无项目，点击右下角 + 新建'), findsOneWidget);
    });

    testWidgets('空项目列表显示新建提示', (tester) async {
      adapter.onGet(
        '/api/projects',
        (server) => server.reply(200, <Map<String, dynamic>>[]),
      );

      await tester.pumpWidget(projectsApp());
      await tester.pumpAndSettle();

      expect(find.text('项目列表（安心养老）'), findsOneWidget);
      expect(find.text('暂无项目，点击右下角 + 新建'), findsOneWidget);
    });

    testWidgets('正常项目列表显示名称和地址', (tester) async {
      adapter.onGet(
        '/api/projects',
        (server) => server.reply(200, [
          {'id': 1, 'name': '王奶奶家', 'address': '幸福路 1 号'},
          {'id': 2, 'name': '李爷爷家', 'address': ''},
        ]),
      );

      await tester.pumpWidget(projectsApp());
      await tester.pumpAndSettle();

      expect(find.text('王奶奶家'), findsOneWidget);
      expect(find.text('幸福路 1 号'), findsOneWidget);
      expect(find.text('李爷爷家'), findsOneWidget);
      expect(find.text('点击进入'), findsOneWidget);
    });

    testWidgets('项目接口错误显示错误状态', (tester) async {
      adapter.onGet(
        '/api/projects',
        (server) => server.reply(500, {'detail': '服务暂不可用'}),
      );

      await tester.pumpWidget(projectsApp());
      await tester.pumpAndSettle();

      expect(find.textContaining('500'), findsOneWidget);
      expect(find.byType(CircularProgressIndicator), findsNothing);
    });
  });
}

import 'dart:async';

import 'package:anjing_app/api/client.dart';
import 'package:anjing_app/api/models.dart';
import 'package:anjing_app/state/auth_store.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http_mock_adapter/http_mock_adapter.dart';
import 'package:shared_preferences/shared_preferences.dart';

class ControlledTokenStorage implements TokenStorage {
  ControlledTokenStorage(this.token);

  String? token;
  final clearCompleter = Completer<void>();
  bool clearCalled = false;

  @override
  Future<void> clear() {
    clearCalled = true;
    token = null;
    return clearCompleter.future;
  }

  @override
  Future<String?> read() async => token;

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
  late AuthStore store;
  RequestOptions? lastRequest;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    dio = Dio(
      BaseOptions(
        baseUrl: 'https://api.test.invalid',
        contentType: Headers.jsonContentType,
      ),
    );
    api = ApiClient(dio: dio);
    dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) {
          lastRequest = options;
          handler.next(options);
        },
      ),
    );
    adapter = DioAdapter(
      dio: dio,
      matcher: const FullHttpRequestMatcher(needsExactBody: true),
    );
    store = AuthStore(api);
  });

  test('登录期间通知 loading 并把 Token 持久化', () async {
    adapter.onPost(
      '/api/auth/login',
      (server) =>
          server.reply(200, {'token': 'persisted-token', 'user': userJson}),
      data: {'email': 'li@example.com', 'password': 'secret123'},
    );
    final loadingStates = <bool>[];
    store.addListener(() => loadingStates.add(store.loading));

    final success = await store.login('li@example.com', 'secret123');
    final preferences = await SharedPreferences.getInstance();

    expect(success, isTrue);
    expect(store.user?.name, '李阿姨');
    expect(preferences.getString('token'), 'persisted-token');
    expect(loadingStates, [true, false]);
  });

  test('恢复已保存 Token 后为请求添加 Authorization', () async {
    SharedPreferences.setMockInitialValues({'token': 'restored-token'});
    adapter.onGet('/api/auth/me', (server) => server.reply(200, userJson));
    adapter.onGet(
      '/api/projects',
      (server) => server.reply(200, <Map<String, dynamic>>[]),
    );

    await store.restore();
    await api.projects();

    expect(lastRequest?.headers['Authorization'], 'Bearer restored-token');
  });

  test('有效 Token 恢复当前用户信息和已认证状态', () async {
    SharedPreferences.setMockInitialValues({'token': 'valid-token'});
    adapter.onGet('/api/auth/me', (server) => server.reply(200, userJson));

    await store.restore();

    expect(store.user?.email, 'li@example.com');
    expect(store.user?.orgName, '安心养老');
    expect(store.authenticated, isTrue);
    expect(store.restoring, isFalse);
    expect(lastRequest?.headers['Authorization'], 'Bearer valid-token');
  });

  test('已保存 Token 无效时清除用户、持久化 Token 和请求头', () async {
    SharedPreferences.setMockInitialValues({'token': 'invalid-token'});
    store.user = AuthUser.fromJson(userJson);
    adapter.onGet(
      '/api/auth/me',
      (server) => server.reply(401, {'detail': 'Invalid token'}),
    );
    adapter.onGet(
      '/api/projects',
      (server) => server.reply(200, <Map<String, dynamic>>[]),
    );

    await store.restore();
    await api.projects();
    final preferences = await SharedPreferences.getInstance();

    expect(store.user, isNull);
    expect(store.status, AuthStatus.unauthenticated);
    expect(preferences.getString('token'), isNull);
    expect(lastRequest?.headers['Authorization'], isNull);
  });

  test('401 登录失败不保存 Token 并向页面暴露错误', () async {
    adapter.onPost(
      '/api/auth/login',
      (server) => server.reply(401, {'detail': 'Invalid token'}),
      data: {'email': 'li@example.com', 'password': 'wrong-password'},
    );

    final success = await store.login('li@example.com', 'wrong-password');
    final preferences = await SharedPreferences.getInstance();

    expect(success, isFalse);
    expect(store.loading, isFalse);
    expect(store.error, contains('登录失败'));
    expect(preferences.getString('token'), isNull);
  });

  test('退出登录清除持久化 Token 和 Authorization', () async {
    SharedPreferences.setMockInitialValues({'token': 'old-token'});
    api.setToken('old-token');
    adapter.onGet(
      '/api/projects',
      (server) => server.reply(200, <Map<String, dynamic>>[]),
    );

    await store.logout();
    await api.projects();
    final preferences = await SharedPreferences.getInstance();

    expect(store.user, isNull);
    expect(preferences.getString('token'), isNull);
    expect(lastRequest?.headers['Authorization'], isNull);
  });

  test('退出登录会等待 Token 存储完成删除后再结束', () async {
    final storage = ControlledTokenStorage('old-token');
    final controlledStore = AuthStore(api, tokenStorage: storage)
      ..user = AuthUser.fromJson(userJson);
    api.setToken('old-token');
    var logoutCompleted = false;

    final logoutFuture = controlledStore.logout().then((_) {
      logoutCompleted = true;
    });
    await Future<void>.delayed(Duration.zero);

    expect(storage.clearCalled, isTrue);
    expect(logoutCompleted, isFalse);
    expect(controlledStore.user, isNull);

    storage.clearCompleter.complete();
    await logoutFuture;

    expect(logoutCompleted, isTrue);
    expect(controlledStore.status, AuthStatus.unauthenticated);
  });
}

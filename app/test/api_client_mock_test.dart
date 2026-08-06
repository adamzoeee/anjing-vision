import 'package:anjing_app/api/client.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http_mock_adapter/http_mock_adapter.dart';

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
  late ApiClient client;
  RequestOptions? lastRequest;

  setUp(() {
    dio = Dio(
      BaseOptions(
        baseUrl: 'https://api.test.invalid',
        contentType: Headers.jsonContentType,
      ),
    );
    client = ApiClient(dio: dio);
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
  });

  group('认证 API', () {
    test('注册发送完整请求并解析用户和 Token', () async {
      adapter.onPost(
        '/api/auth/register',
        (server) =>
            server.reply(200, {'token': 'register-token', 'user': userJson}),
        data: {
          'org_name': '安心养老',
          'name': '李阿姨',
          'email': 'li@example.com',
          'password': 'secret123',
        },
      );

      final result = await client.register(
        orgName: '安心养老',
        name: '李阿姨',
        email: 'li@example.com',
        password: 'secret123',
      );

      expect(result.token, 'register-token');
      expect(result.user.name, '李阿姨');
      expect(result.user.orgName, '安心养老');
    });

    test('登录发送邮箱密码并解析正常响应', () async {
      adapter.onPost(
        '/api/auth/login',
        (server) =>
            server.reply(200, {'token': 'login-token', 'user': userJson}),
        data: {'email': 'li@example.com', 'password': 'secret123'},
      );

      final result = await client.login(
        email: 'li@example.com',
        password: 'secret123',
      );

      expect(result.token, 'login-token');
      expect(result.user.email, 'li@example.com');
      expect(result.user.role, 'member');
    });

    test('登录后保存 Token 并用于后续 Authorization 请求头', () async {
      adapter.onPost(
        '/api/auth/login',
        (server) =>
            server.reply(200, {'token': 'saved-token', 'user': userJson}),
        data: {'email': 'li@example.com', 'password': 'secret123'},
      );
      adapter.onGet(
        '/api/projects',
        (server) => server.reply(200, <Map<String, dynamic>>[]),
      );

      await client.login(email: 'li@example.com', password: 'secret123');
      await client.projects();

      expect(lastRequest?.headers['Authorization'], 'Bearer saved-token');
    });
  });

  group('项目、扫描与报告 API', () {
    test('查询项目列表并解析正常响应', () async {
      adapter.onGet(
        '/api/projects',
        (server) => server.reply(200, [
          {'id': 1, 'name': '王奶奶家', 'address': '幸福路 1 号'},
          {'id': 2, 'name': '李爷爷家'},
        ]),
      );

      final projects = await client.projects();

      expect(projects, hasLength(2));
      expect(projects.first.name, '王奶奶家');
      expect(projects.first.address, '幸福路 1 号');
      expect(projects.last.address, '');
    });

    test('创建项目发送名称地址并解析项目', () async {
      adapter.onPost(
        '/api/projects',
        (server) =>
            server.reply(201, {'id': 3, 'name': '赵奶奶家', 'address': '康乐街 8 号'}),
        data: {'name': '赵奶奶家', 'address': '康乐街 8 号'},
      );

      final project = await client.createProject('赵奶奶家', '康乐街 8 号');

      expect(project.id, 3);
      expect(project.name, '赵奶奶家');
      expect(project.address, '康乐街 8 号');
    });

    test('创建扫描发送采集类型并解析扫描', () async {
      adapter.onPost(
        '/api/projects/3/scans',
        (server) => server.reply(201, {
          'id': 12,
          'project_id': 3,
          'status': 'created',
          'progress': 0,
          'message': '等待上传',
          'capture_type': 'video',
        }),
        data: {'capture_type': 'video'},
      );

      final scan = await client.createScan(3, 'video');

      expect(scan.id, 12);
      expect(scan.projectId, 3);
      expect(scan.captureType, 'video');
      expect(scan.status, 'created');
    });

    test('查询扫描状态并解析进度', () async {
      adapter.onGet(
        '/api/scans/12',
        (server) => server.reply(200, {
          'id': 12,
          'project_id': 3,
          'status': 'training',
          'progress': 65,
          'message': '正在重建',
          'capture_type': 'video',
        }),
      );

      final scan = await client.scanStatus(12);

      expect(scan.status, 'training');
      expect(scan.progress, 65);
      expect(scan.message, '正在重建');
    });

    test('获取报告并解析评分、风险和建议', () async {
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
          ],
          'advice': ['建议拓宽门洞'],
          'images': ['/reports/12/door.jpg'],
          'calibrated': 1,
        }),
      );

      final report = await client.report(12);

      expect(report.scanId, 12);
      expect(report.score, 78.5);
      expect(report.risks.single.code, 'door_width');
      expect(report.advice, ['建议拓宽门洞']);
      expect(report.images, ['/reports/12/door.jpg']);
    });
  });

  group('异常响应', () {
    test('服务端错误作为 DioException 返回给调用方', () async {
      adapter.onGet(
        '/api/projects',
        (server) => server.reply(500, {'detail': '服务暂不可用'}),
      );

      await expectLater(
        client.projects(),
        throwsA(
          isA<DioException>().having(
            (error) => error.response?.statusCode,
            'statusCode',
            500,
          ),
        ),
      );
    });

    test('网络异常作为 connectionError 返回且不访问真实网络', () async {
      adapter.onGet(
        '/api/scans/12',
        (server) => server.throws(
          0,
          DioException(
            requestOptions: RequestOptions(path: '/api/scans/12'),
            type: DioExceptionType.connectionError,
            error: 'offline',
          ),
        ),
      );

      await expectLater(
        client.scanStatus(12),
        throwsA(
          isA<DioException>().having(
            (error) => error.type,
            'type',
            DioExceptionType.connectionError,
          ),
        ),
      );
    });
  });
}

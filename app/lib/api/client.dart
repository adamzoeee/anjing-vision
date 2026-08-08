import 'dart:convert';

import 'package:dio/dio.dart';

import 'models.dart';

class ApiClient {
  final Dio dio;
  String? _token;
  ApiClient({String? baseUrl, Dio? dio})
    : dio =
          dio ??
          Dio(
            BaseOptions(
              baseUrl: baseUrl ?? 'http://10.0.2.2:8000',
              connectTimeout: const Duration(seconds: 15),
              receiveTimeout: const Duration(seconds: 60),
            ),
          ) {
    if (dio != null && baseUrl != null) {
      this.dio.options.baseUrl = baseUrl;
    }
    this.dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (o, h) {
          if (_token != null) o.headers['Authorization'] = 'Bearer $_token';
          h.next(o);
        },
      ),
    );
  }

  void setToken(String? t) => _token = t;

  Map<String, String> get authorizationHeaders => _token == null
      ? const {}
      : {'Authorization': 'Bearer $_token'};

  Future<AuthUser> me() async =>
      AuthUser.fromJson((await dio.get('/api/auth/me')).data);

  Future<({String token, AuthUser user})> register({
    required String orgName,
    required String name,
    required String email,
    required String password,
  }) async {
    final r = await dio.post(
      '/api/auth/register',
      data: {
        'org_name': orgName,
        'name': name,
        'email': email,
        'password': password,
      },
    );
    _token = r.data['token'];
    return (token: _token!, user: AuthUser.fromJson(r.data['user']));
  }

  Future<({String token, AuthUser user})> login({
    required String email,
    required String password,
  }) async {
    final r = await dio.post(
      '/api/auth/login',
      data: {'email': email, 'password': password},
    );
    _token = r.data['token'];
    return (token: _token!, user: AuthUser.fromJson(r.data['user']));
  }

  Future<List<Project>> projects() =>
      _allPages('/api/projects', (json) => Project.fromJson(json));

  Future<Project> createProject(String name, String address) async =>
      Project.fromJson(
        (await dio.post(
          '/api/projects',
          data: {'name': name, 'address': address},
        )).data,
      );

  Future<Scan> createScan(int projectId, String captureType) async =>
      Scan.fromJson(
        (await dio.post(
          '/api/projects/$projectId/scans',
          data: {'capture_type': captureType},
        )).data,
      );

  Future<List<Scan>> listScans(int projectId) => _allPages(
    '/api/projects/$projectId/scans',
    (json) => Scan.fromJson(json),
  );

  Future<void> uploadVideo(int scanId, String filePath, String filename) async {
    // 流式上传（不整文件读入内存）
    await dio.post(
      '/api/scans/$scanId/upload',
      data: FormData.fromMap({
        'files': await MultipartFile.fromFile(filePath, filename: filename),
      }),
      options: Options(contentType: 'multipart/form-data'),
    );
  }

  Future<Scan> scanStatus(int scanId) async =>
      Scan.fromJson((await dio.get('/api/scans/$scanId')).data);

  Future<Report> report(int scanId) async =>
      Report.fromJson((await dio.get('/api/reports/scans/$scanId')).data);

  Future<Map<String, dynamic>> compare(
    int beforeScanId,
    int afterScanId,
  ) async => (await dio.get(
    '/api/reports/compare',
    queryParameters: {
      'before_scan_id': beforeScanId,
      'after_scan_id': afterScanId,
    },
  )).data;

  Future<List<T>> _allPages<T>(
    String path,
    T Function(dynamic json) parse,
  ) async {
    final result = <T>[];
    var offset = 0;
    int? observedPageSize;
    String? previousPageSignature;
    while (true) {
      final response = await dio.get(
        path,
        queryParameters: {'offset': offset},
      );
      final rawPage = response.data as List;
      if (rawPage.isEmpty) return result;
      final pageSignature = jsonEncode(rawPage);
      if (pageSignature == previousPageSignature) return result;
      previousPageSignature = pageSignature;
      final page = rawPage.map<T>(parse).toList();
      result.addAll(page);
      final serverPageSize = int.tryParse(
        response.headers.value('x-page-size') ?? '',
      );
      observedPageSize ??= serverPageSize ?? page.length;
      if (page.length < observedPageSize) {
        return result;
      }
      offset += page.length;
    }
  }
}

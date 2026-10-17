import 'dart:typed_data';

import 'package:anjing_app/api/client.dart';
import 'package:anjing_app/api/live_capture.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http_mock_adapter/http_mock_adapter.dart';

void main() {
  test('缺失及 null 统计不伪造消费 FPS', () {
    final status = LiveCaptureStatus.fromJson({'processed_fps': null});
    expect(status.enabled, false);
    expect(status.state, 'unknown');
    expect(status.processedFps, isNull);
    expect(status.width, isNull);
    expect(status.frameCount, 0);
    final running = LiveCaptureStatus.fromJson({
      'enabled': true,
      'state': 'running',
      'input_fps': 9,
      'source': {'type': 'file'},
      'last_error': {'message': '输入断开'},
    });
    expect(running.inputFps, 9.0);
    expect(running.sourceType, 'file');
    expect(running.lastError, '输入断开');
  });

  test('查询与启停复用认证，预览204正常降级', () async {
    final dio = Dio(BaseOptions(baseUrl: 'https://capture.invalid'));
    final api = ApiClient(dio: dio)..setToken('capture-token');
    final adapter = DioAdapter(dio: dio);
    RequestOptions? request;
    dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) {
          request = options;
          handler.next(options);
        },
      ),
    );
    adapter.onGet(
      '/api/live-capture/status',
      (server) => server.reply(200, {'state': 'stopped', 'enabled': true}),
    );
    adapter.onPost(
      '/api/live-capture/start',
      (server) => server.reply(200, {'state': 'starting'}),
    );
    adapter.onPost(
      '/api/live-capture/stop',
      (server) => server.reply(200, {'state': 'stopped'}),
    );
    adapter.onGet(
      '/api/live-capture/frame.jpg',
      (server) => server.reply(204, null),
    );
    expect((await api.liveCaptureStatus()).enabled, true);
    expect((await api.startLiveCapture()).state, 'starting');
    expect((await api.stopLiveCapture()).state, 'stopped');
    expect(await api.liveCaptureFrame(), isNull);
    expect(request?.headers['Authorization'], 'Bearer capture-token');
    expect(request?.responseType, ResponseType.bytes);
  });

  test('预览按字节读取，不将JWT拼到URL', () async {
    final dio = Dio(BaseOptions(baseUrl: 'https://capture.invalid'));
    final api = ApiClient(dio: dio)..setToken('capture-token');
    dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) {
          expect(options.queryParameters, isEmpty);
          expect(options.headers['Authorization'], 'Bearer capture-token');
          handler.resolve(
            Response(
              requestOptions: options,
              statusCode: 200,
              data: Uint8List.fromList([255, 216, 255, 217]),
            ),
          );
        },
      ),
    );
    expect(await api.liveCaptureFrame(), [255, 216, 255, 217]);
  });
}

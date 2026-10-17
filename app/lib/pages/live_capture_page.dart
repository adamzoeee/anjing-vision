import 'dart:async';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/client.dart';
import '../api/live_capture.dart';
import '../state/auth_store.dart';

/// Preview polls independently; navigating away never stops shared consumers.
class LiveCapturePage extends StatefulWidget {
  const LiveCapturePage({super.key});

  @override
  State<LiveCapturePage> createState() => _LiveCapturePageState();
}

class _LiveCapturePageState extends State<LiveCapturePage>
    with WidgetsBindingObserver {
  Timer? _statusTimer, _frameTimer;
  CancelToken _requests = CancelToken();
  LiveCaptureStatus? _status;
  Uint8List? _frame;
  String? _error;
  bool _statusBusy = false, _frameBusy = false, _controlBusy = false;
  bool _preview = true, _active = true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _poll();
  }

  void _poll() {
    _refresh();
    _statusTimer = Timer.periodic(
      const Duration(seconds: 1),
      (_) => _refresh(),
    );
    _frameTimer = Timer.periodic(
      const Duration(milliseconds: 250),
      (_) => _refreshFrame(),
    );
  }

  bool _current(CancelToken token) =>
      mounted && _active && identical(token, _requests) && !token.isCancelled;

  void _showError(Object error, CancelToken token) {
    if (!_current(token) ||
        (error is DioException && CancelToken.isCancel(error))) {
      return;
    }
    String message = '无法连接实时采集服务，请检查后端或登录状态';
    if (error is DioException) {
      final data = error.response?.data;
      if (data is Map && data['error'] is Map) {
        message = (data['error'] as Map)['message'] as String? ?? message;
      }
    }
    setState(() => _error = message);
  }

  Future<void> _refresh() async {
    if (!_active || _statusBusy || _controlBusy) return;
    _statusBusy = true;
    final token = _requests;
    try {
      final status = await context.read<ApiClient>().liveCaptureStatus(
        cancelToken: token,
      );
      if (!_current(token)) return;
      setState(() {
        _status = status;
        _error = null;
        if (!status.previewEnabled || !status.enabled || !status.threadAlive) {
          _frame = null;
        }
      });
    } catch (error) {
      _showError(error, token);
    } finally {
      _statusBusy = false;
    }
  }

  Future<void> _refreshFrame() async {
    final status = _status;
    if (!_active ||
        !_preview ||
        _frameBusy ||
        status == null ||
        !status.enabled ||
        !status.previewEnabled ||
        status.state != 'running') {
      return;
    }
    _frameBusy = true;
    final token = _requests;
    try {
      final image = await context.read<ApiClient>().liveCaptureFrame(
        cancelToken: token,
      );
      if (_current(token) && _preview && _status?.state == 'running') {
        setState(() => _frame = image);
      }
    } catch (error) {
      _showError(error, token);
    } finally {
      _frameBusy = false;
    }
  }

  Future<void> _control(bool start) async {
    if (_controlBusy) return;
    setState(() => _controlBusy = true);
    final token = _requests;
    try {
      final api = context.read<ApiClient>();
      final status = start
          ? await api.startLiveCapture(cancelToken: token)
          : await api.stopLiveCapture(cancelToken: token);
      if (!_current(token)) return;
      setState(() {
        _status = status;
        _frame = null;
        _error = null;
      });
    } catch (error) {
      _showError(error, token);
    } finally {
      if (mounted) setState(() => _controlBusy = false);
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      if (_active) return;
      _active = true;
      _requests = CancelToken();
      _poll();
    } else {
      _active = false;
      _statusTimer?.cancel();
      _frameTimer?.cancel();
      _requests.cancel('Preview backgrounded');
      _frame = null;
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _active = false;
    _statusTimer?.cancel();
    _frameTimer?.cancel();
    _requests.cancel('Preview closed');
    super.dispose();
  }

  String _metric(double? value) => value?.toStringAsFixed(1) ?? '—';

  @override
  Widget build(BuildContext context) {
    final status = _status;
    final admin = context.watch<AuthStore>().user?.role == 'admin';
    const states = {
      'stopped': '已停止',
      'starting': '正在启动',
      'running': '采集中',
      'reconnecting': '正在重连',
      'error': '采集错误',
    };
    return Scaffold(
      appBar: AppBar(title: const Text('实时视频预览')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text(
            '预览后端 USB 摄像头或管理员配置的本地视频，不使用手机摄像头。'
            '默认不录制、不保存原始画面，不采集音频。',
          ),
          if (_error != null)
            Text(_error!, key: const Key('capture-api-error')),
          if (status == null && _error == null) const Text('正在查询采集状态…'),
          if (status != null) ...[
            if (!status.enabled) const Text('实时采集未启用，请管理员配置 REALTIME_ENABLED'),
            Text('状态：${states[status.state] ?? status.state}'),
            Text(
              '输入：${status.sourceType} · '
              '分辨率：${status.width ?? "—"} × ${status.height ?? "—"}',
            ),
            Text(
              '实际采集 FPS：${_metric(status.inputFps)} · '
              '下游消费 FPS：${_metric(status.processedFps)}',
            ),
            Text(
              '帧数：${status.frameCount} · 队列：${status.queueDepth} · '
              '丢帧：${status.droppedFrames} · 重连：${status.reconnectCount}',
            ),
            Text(
              '读帧耗时：${_metric(status.captureLatencyMs)} ms · '
              '最新帧龄：${_metric(status.lastFrameAgeMs)} ms',
            ),
            const Text('尚未接入 AI 推理；消费 FPS / AI 端到端延迟未测不报。'),
            if (status.lastError != null)
              Text(
                '最近错误：${status.lastError}',
                key: const Key('capture-source-error'),
              ),
            if (status.reason == 'end_of_file') const Text('视频播放完毕，不自动重连'),
            if (admin)
              Wrap(
                spacing: 12,
                children: [
                  FilledButton(
                    key: const Key('capture-start'),
                    onPressed:
                        status.enabled && !status.threadAlive && !_controlBusy
                        ? () => _control(true)
                        : null,
                    child: const Text('启动采集'),
                  ),
                  OutlinedButton(
                    key: const Key('capture-stop'),
                    onPressed: status.threadAlive && !_controlBusy
                        ? () => _control(false)
                        : null,
                    child: const Text('停止采集'),
                  ),
                ],
              )
            else
              const Text('仅管理员可启动或停止；同机构成员可以预览'),
            SwitchListTile(
              title: const Text('显示预览（关闭不会停止其他消费者）'),
              value: _preview && status.previewEnabled,
              onChanged: status.previewEnabled
                  ? (value) {
                      setState(() {
                        _preview = value;
                        if (!value) _frame = null;
                      });
                    }
                  : null,
            ),
            if (_frame != null)
              Image.memory(
                _frame!,
                gaplessPlayback: true,
                errorBuilder: (_, _, _) => const Text('画面解码失败'),
              )
            else
              const SizedBox(height: 180, child: Center(child: Text('暂无预览画面'))),
          ],
        ],
      ),
    );
  }
}

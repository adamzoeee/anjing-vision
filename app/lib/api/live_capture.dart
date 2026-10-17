/// Arrival metrics are not inference metrics or camera poses.
class LiveCaptureStatus {
  LiveCaptureStatus.fromJson(Map<String, dynamic> json)
    : enabled = json['enabled'] == true,
      state = json['state'] as String? ?? 'unknown',
      reason = json['reason'] as String?,
      previewEnabled = json['preview_enabled'] == true,
      threadAlive = json['thread_alive'] == true,
      sourceType = (json['source'] as Map?)?['type'] as String? ?? 'unknown',
      width = (json['width'] as num?)?.toInt(),
      height = (json['height'] as num?)?.toInt(),
      inputFps = (json['input_fps'] as num?)?.toDouble(),
      processedFps = (json['processed_fps'] as num?)?.toDouble(),
      frameCount = (json['frame_count'] as num?)?.toInt() ?? 0,
      queueDepth = (json['queue_depth'] as num?)?.toInt() ?? 0,
      droppedFrames = (json['dropped_frames'] as num?)?.toInt() ?? 0,
      reconnectCount = (json['reconnect_count'] as num?)?.toInt() ?? 0,
      lastFrameAgeMs = (json['last_frame_age_ms'] as num?)?.toDouble(),
      captureLatencyMs = (json['capture_latency_ms'] as num?)?.toDouble(),
      lastError = (json['last_error'] as Map?)?['message'] as String?;

  final bool enabled, previewEnabled, threadAlive;
  final String state, sourceType;
  final String? reason, lastError;
  final int? width, height;
  final double? inputFps, processedFps, lastFrameAgeMs, captureLatencyMs;
  final int frameCount, queueDepth, droppedFrames, reconnectCount;
}

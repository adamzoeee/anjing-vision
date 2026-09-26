import 'dart:io';
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

import '../models/scheme_layout.dart';
import '../widgets/scheme_plan_painter.dart';

/// 离屏渲染叠影图：与屏幕上的 CustomPainter 共用 paintSchemePlan。
Future<Uint8List> renderSchemePng(SchemeScene scene,
    {double pixelRatio = 3.0, double size = 360}) async {
  final recorder = ui.PictureRecorder();
  final canvas = Canvas(recorder, Rect.fromLTWH(0, 0, size, size));
  canvas.drawRect(Rect.fromLTWH(0, 0, size, size), Paint()..color = const Color(0xFFF6F4EE));
  paintSchemePlan(canvas, scene);
  final picture = recorder.endRecording();
  final image = await picture.toImage((size * pixelRatio).round(), (size * pixelRatio).round());
  final data = await image.toByteData(format: ui.ImageByteFormat.png);
  return data!.buffer.asUint8List();
}

Future<File> exportSchemePdf({
  required Dio dio,
  required int scanId,
  required int schemeId,
  required Uint8List png,
  required String schemeName,
}) async {
  final response = await dio.post(
    '/api/assistant/scans/$scanId/schemes/$schemeId/export.pdf',
    data: FormData.fromMap({'plan': MultipartFile.fromBytes(png, filename: 'plan.png')}),
    options: Options(responseType: ResponseType.bytes),
  );
  final dir = await getTemporaryDirectory();
  final file = File('${dir.path}/$schemeName.pdf');
  await file.writeAsBytes(response.data as List<int>);
  return file;
}

Future<void> shareFile(File file) async {
  await SharePlus.instance.share(
    ShareParams(files: [XFile(file.path)], text: '改造方案（沙盒模拟 · 非实测）'),
  );
}

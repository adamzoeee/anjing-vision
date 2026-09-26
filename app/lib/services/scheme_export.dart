import 'dart:io';
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

import '../models/scheme_layout.dart';
import '../widgets/scheme_plan_painter.dart';

/// 导出产物（PNG 页脚 / PDF / 分享文案）统一的「非实测」声明标签。
const String kSchemeDisclaimerLabel = '沙盒模拟 · 非实测';

/// 页脚与分享文案：声明标签 + 可选方案名，保证只有一处文案来源。
String schemeFooterText([String? schemeName]) {
  final name = schemeName?.trim() ?? '';
  return name.isEmpty ? kSchemeDisclaimerLabel : '$kSchemeDisclaimerLabel · $name';
}

/// 导出文件名：替换 Windows 非法字符并裁掉首尾空白。
String sanitizeSchemeFileName(String schemeName) {
  final safe = schemeName.replaceAll(RegExp(r'[\\/:*?"<>|]'), '-').trim();
  return safe.isEmpty ? 'scheme' : safe;
}

/// 离屏渲染叠影图：与屏幕上的 CustomPainter 共用 paintSchemePlan，
/// 并在底部左侧合成声明页脚（文案由调用方传入，避免写死两处）。
Future<Uint8List> renderSchemePng(SchemeScene scene,
    {double pixelRatio = 3.0, double size = 360,
     String footer = kSchemeDisclaimerLabel}) async {
  final recorder = ui.PictureRecorder();
  final canvas = Canvas(recorder, Rect.fromLTWH(0, 0, size, size));
  canvas.drawRect(Rect.fromLTWH(0, 0, size, size), Paint()..color = const Color(0xFFF6F4EE));
  paintSchemePlan(canvas, scene);
  paintSchemeFooter(canvas, footer, size: size);
  final picture = recorder.endRecording();
  final image = await picture.toImage((size * pixelRatio).round(), (size * pixelRatio).round());
  final data = await image.toByteData(format: ui.ImageByteFormat.png);
  return data!.buffer.asUint8List();
}

/// 页脚排版器：paintSchemeFooter 与测试共用，测试据此断言真实绘制文案。
TextPainter schemeFooterPainter(String text, {required double size}) => TextPainter(
      text: TextSpan(
        text: text.trim(),
        style: const TextStyle(fontSize: 12, color: kSchemeInk, fontWeight: FontWeight.w600),
      ),
      textDirection: TextDirection.ltr,
      maxLines: 1,
      ellipsis: '…',
    )..layout(maxWidth: size - 16);

/// 底部左侧页脚绘制：导出与测试共用同一实现。
void paintSchemeFooter(Canvas canvas, String text, {required double size}) {
  final trimmed = text.trim();
  if (trimmed.isEmpty) return;
  final painter = schemeFooterPainter(trimmed, size: size);
  final origin = Offset(8, size - painter.height - 8);
  canvas.drawRRect(
    RRect.fromRectAndRadius(
      Rect.fromLTWH(origin.dx - 4, origin.dy - 2, painter.width + 8, painter.height + 4),
      const Radius.circular(4),
    ),
    Paint()..color = const Color(0xE6F6F4EE),
  );
  painter.paint(canvas, origin);
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
  final file = File('${dir.path}/${sanitizeSchemeFileName(schemeName)}.pdf');
  await file.writeAsBytes(response.data as List<int>);
  return file;
}

/// 分享导出文件；默认沿用统一声明文案，调用方可传入自定义文案。
Future<void> shareFile(File file, {String? text}) => SharePlus.instance.share(
      ShareParams(files: [XFile(file.path)], text: text ?? kSchemeDisclaimerLabel),
    );

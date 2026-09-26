import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../models/scheme_layout.dart';

const kSchemeAccent = Color(0xFF0071E3);
const kSchemeGhost = Color(0xFFB6BCC6);
const kSchemeAssumed = Color(0xFFFF9F0A);
const kSchemeInk = Color(0xFF5B6470);

/// 屏幕与导出共用的绘制入口。
void paintSchemePlan(Canvas canvas, SchemeScene scene, {Color accent = kSchemeAccent}) {
  canvas.drawRect(scene.roomRect, Paint()..color = const Color(0xFFFBFBFD));
  canvas.drawRect(scene.roomRect,
      Paint()..style = PaintingStyle.stroke..strokeWidth = 2..color = kSchemeGhost);

  Rect rectOf(Offset center, Size size) =>
      Rect.fromCenter(center: center, width: size.width, height: size.height);

  for (final box in scene.boxes) {
    if (box.changed || box.removed) {
      final ghost = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.5
        ..color = kSchemeGhost;
      canvas.drawPath(_dashed(Path()..addRect(rectOf(box.beforeCenter, box.size))), ghost);
    }
    if (box.hidden) continue;
    final rect = rectOf(box.afterCenter, box.size);
    canvas.drawRect(rect,
        Paint()..color = box.assumed ? kSchemeAssumed.withValues(alpha: 0.18) : Colors.white);
    canvas.drawRect(rect, Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = box.changed ? 2.5 : 1.5
      ..color = box.assumed ? kSchemeAssumed : (box.changed ? accent : kSchemeInk));
  }

  for (final box in scene.boxes.where((b) => b.changed && !b.hidden)) {
    final from = box.beforeCenter;
    final to = box.afterCenter;
    if ((from - to).distance < 1) continue;
    canvas.drawLine(from, to, Paint()..color = accent..strokeWidth = 2);
    _arrowHead(canvas, from, to, accent);
    final meters = (from.dy - to.dy) / scene.metersToPixels;
    _text(canvas, '↓${meters.abs().toStringAsFixed(2)}m',
        Offset(math.max(from.dx, to.dx) + 6, (from.dy + to.dy) / 2), accent);
  }
}

class SchemePlanPainter extends CustomPainter {
  SchemePlanPainter({
    required this.before, required this.after,
    required this.changedIds, required this.removedIds,
  });

  final SchemeLayout before;
  final SchemeLayout after;
  final Set<String> changedIds;
  final Set<String> removedIds;

  @override
  void paint(Canvas canvas, Size size) {
    final scene = buildSchemeScene(
      before: before, after: after, size: size, padding: 16,
      changedIds: changedIds, removedIds: removedIds,
    );
    paintSchemePlan(canvas, scene);
  }

  @override
  bool shouldRepaint(covariant SchemePlanPainter old) =>
      old.before != before || old.after != after ||
      old.changedIds != changedIds || old.removedIds != removedIds;
}

Path _dashed(Path source) {
  const dash = 6.0, gap = 4.0;
  final result = Path();
  for (final metric in source.computeMetrics()) {
    var distance = 0.0;
    while (distance < metric.length) {
      final next = math.min(distance + dash, metric.length);
      result.addPath(metric.extractPath(distance, next), Offset.zero);
      distance = next + gap;
    }
  }
  return result;
}

void _arrowHead(Canvas canvas, Offset from, Offset to, Color color) {
  final angle = math.atan2(to.dy - from.dy, to.dx - from.dx);
  const size = 7.0;
  final path = Path()
    ..moveTo(to.dx, to.dy)
    ..lineTo(to.dx - size * math.cos(angle - 0.4), to.dy - size * math.sin(angle - 0.4))
    ..lineTo(to.dx - size * math.cos(angle + 0.4), to.dy - size * math.sin(angle + 0.4))
    ..close();
  canvas.drawPath(path, Paint()..color = color);
}

void _text(Canvas canvas, String value, Offset at, Color color) {
  final painter = TextPainter(
    text: TextSpan(text: value, style: TextStyle(color: color, fontSize: 11)),
    textDirection: TextDirection.ltr,
  )..layout();
  painter.paint(canvas, at);
}

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

  // 小画布也保持标签可读：字号随房间像素尺寸收缩，下限 9px。
  final labelSize = (scene.roomRect.width / 30).clamp(9.0, 12.0).toDouble();

  for (final box in scene.boxes) {
    final ghostRect = rectOf(box.beforeCenter, box.beforeSize);
    if (box.changed || box.removed) {
      final ghost = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.5
        ..color = kSchemeGhost;
      // 幽灵框用改前尺寸，改尺寸的物体也能看到真实原始足迹。
      canvas.drawPath(_dashed(Path()..addRect(ghostRect)), ghost);
      if (box.removed) {
        final removedLabel = schemeBoxLabel(box);
        if (removedLabel != null) {
          _label(canvas, removedLabel, ghostRect, kSchemeGhost, scene, labelSize);
        }
      }
    }
    if (box.hidden) continue;
    final rect = rectOf(box.afterCenter, box.afterSize);
    canvas.drawRect(rect,
        Paint()..color = box.assumed ? kSchemeAssumed.withValues(alpha: 0.18) : Colors.white);
    canvas.drawRect(rect, Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = box.changed ? 2.5 : 1.5
      ..color = box.assumed ? kSchemeAssumed : (box.changed ? accent : kSchemeInk));
    final label = schemeBoxLabel(box);
    if (label != null) {
      _label(canvas, label, rect, box.assumed ? kSchemeAssumed : accent, scene, labelSize);
    }
  }

  for (final box in scene.boxes.where((b) => b.changed && !b.hidden)) {
    final from = box.beforeCenter;
    final to = box.afterCenter;
    final moved = (from - to).distance;
    if (moved < 1) continue;
    canvas.drawLine(from, to, Paint()..color = accent..strokeWidth = 2);
    _arrowHead(canvas, from, to, accent);
    final meters = moved / scene.metersToPixels;
    final dx = to.dx - from.dx;
    final dy = to.dy - from.dy;
    final hint = dx.abs() >= dy.abs() ? '↔' : (dy < 0 ? '↑' : '↓');
    _text(canvas, '$hint${meters.toStringAsFixed(2)}m',
        Offset(math.max(from.dx, to.dx) + 6, (from.dy + to.dy) / 2), accent);
  }

  // 门/窗洞宽：用蓝色开洞条表示，改造前后宽度变化直接可见。
  for (final door in scene.doors) {
    _openingBar(canvas, door.center, door.beforeWidthPx, door.afterWidthPx,
        door.changed, accent);
    _text(canvas, '门 ${door.widthMeters.toStringAsFixed(2)}m',
        Offset(door.center.dx - door.afterWidthPx / 2, door.center.dy - 14), accent,
        fontSize: labelSize);
  }
  for (final window in scene.windows) {
    _openingBar(canvas, window.center, window.beforeWidthPx, window.afterWidthPx,
        window.changed, kSchemeGhost);
    _text(canvas, '窗 ${window.widthMeters.toStringAsFixed(2)}m',
        Offset(window.center.dx - window.afterWidthPx / 2, window.center.dy - 14), kSchemeInk,
        fontSize: labelSize);
  }
}

/// 在房间轮廓上绘制一条洞宽条：改造前用浅色，改造后用实色覆盖。
void _openingBar(Canvas canvas, Offset center, double beforeWidth, double afterWidth,
    bool changed, Color color) {
  if (changed) {
    canvas.drawRect(
        Rect.fromCenter(center: center, width: beforeWidth, height: 10),
        Paint()..color = color.withValues(alpha: 0.28));
  }
  canvas.drawRect(
      Rect.fromCenter(center: center, width: afterWidth, height: 6),
      Paint()..color = color);
}

/// 单个物体在叠影图上的标签：已移除 > 假设放置 > 改动物体用普通名称。
String? schemeBoxLabel(SchemeBoxScene box) {
  if (box.removed) return '${box.label} · 已移除';
  if (box.assumed) return '${box.label} · 假设放置';
  if (box.changed) return box.label;
  return null;
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

void _text(Canvas canvas, String value, Offset at, Color color,
    {double fontSize = 11, FontWeight? weight, double? maxWidth}) {
  final painter = TextPainter(
    text: TextSpan(
      text: value,
      style: TextStyle(color: color, fontSize: fontSize, fontWeight: weight),
    ),
    textDirection: TextDirection.ltr,
    maxLines: 1,
    ellipsis: '…',
  )..layout(maxWidth: maxWidth ?? double.infinity);
  painter.paint(canvas, at);
}

/// 在物体框旁绘制标签：优先放框上方，空间不足时收进框内左上角。
void _label(Canvas canvas, String value, Rect anchor, Color color,
    SchemeScene scene, double fontSize) {
  final trimmed = value.trim();
  if (trimmed.isEmpty) return;
  const weight = FontWeight.w600;
  final maxWidth = math.max(scene.roomRect.width, 24.0);
  final measured = TextPainter(
    text: TextSpan(text: trimmed, style: TextStyle(fontSize: fontSize, fontWeight: weight)),
    textDirection: TextDirection.ltr,
    maxLines: 1,
    ellipsis: '…',
  )..layout(maxWidth: maxWidth);
  final bounds = scene.roomRect;
  var dx = anchor.left;
  var dy = anchor.top - measured.height - 2;
  if (dy < bounds.top + 2) dy = anchor.top + 2;
  dx = dx
      .clamp(bounds.left + 2, math.max(bounds.right - measured.width - 2, bounds.left + 2))
      .toDouble();
  dy = dy
      .clamp(bounds.top + 2, math.max(bounds.bottom - measured.height - 2, bounds.top + 2))
      .toDouble();
  _text(canvas, trimmed, Offset(dx, dy), color,
      fontSize: fontSize, weight: weight, maxWidth: maxWidth);
}

import 'dart:math' as math;
import 'dart:ui';

class SchemeBox {
  const SchemeBox({
    required this.id, required this.label, required this.kind,
    required this.cx, required this.cy, required this.sx, required this.sy,
    required this.rotationDeg, required this.assumed,
  });

  final String id;
  final String label;
  final String kind;
  final double cx;
  final double cy;
  final double sx;
  final double sy;
  final double rotationDeg;
  final bool assumed;

  factory SchemeBox.fromJson(Map<String, dynamic> json) {
    final center = (json['center'] as List? ?? const [0, 0, 0])
        .map((v) => (v as num).toDouble()).toList();
    final size = (json['size'] as List? ?? const [0, 0, 0])
        .map((v) => (v as num).toDouble()).toList();
    return SchemeBox(
      id: json['id']?.toString() ?? '',
      label: (json['label'] ?? json['category'] ?? 'object').toString(),
      kind: (json['kind'] ?? 'object').toString(),
      cx: center.isNotEmpty ? center[0] : 0,
      cy: center.length > 1 ? center[1] : 0,
      sx: size.isNotEmpty ? size[0] : 0,
      sy: size.length > 1 ? size[1] : 0,
      rotationDeg: (json['rotation_z_deg'] as num?)?.toDouble() ?? 0,
      assumed: json['assumed'] == true,
    );
  }
}

class SchemeLayout {
  const SchemeLayout({
    required this.minX, required this.minY, required this.maxX, required this.maxY,
    required this.objects,
  });

  final double minX, minY, maxX, maxY;
  final List<SchemeBox> objects;

  factory SchemeLayout.fromJson(Map<String, dynamic> json) {
    final room = (json['room'] as Map?)?.cast<String, dynamic>() ?? const {};
    final bounds = (room['bounds_xy'] as Map?)?.cast<String, dynamic>() ?? const {};
    final min = (bounds['min'] as List? ?? const [0, 0]).map((v) => (v as num).toDouble()).toList();
    final max = (bounds['max'] as List? ?? const [4, 4]).map((v) => (v as num).toDouble()).toList();
    final objects = (json['objects'] as List? ?? const [])
        .whereType<Map>()
        .map((item) => SchemeBox.fromJson(item.cast<String, dynamic>()))
        .toList();
    return SchemeLayout(
      minX: min.isNotEmpty ? min[0] : 0, minY: min.length > 1 ? min[1] : 0,
      maxX: max.isNotEmpty ? max[0] : 4, maxY: max.length > 1 ? max[1] : 4,
      objects: objects,
    );
  }
}

class SchemeBoxScene {
  const SchemeBoxScene({
    required this.id, required this.label, required this.assumed, required this.changed,
    required this.removed, required this.beforeCenter, required this.afterCenter,
    required this.size, required this.hidden,
  });

  final String id;
  final String label;
  final bool assumed;
  final bool changed;
  final bool removed;
  final Offset beforeCenter;
  final Offset afterCenter;
  final Size size;
  final bool hidden;
}

class SchemeScene {
  const SchemeScene({required this.roomRect, required this.boxes, required this.metersToPixels});

  final Rect roomRect;
  final List<SchemeBoxScene> boxes;
  final double metersToPixels;
}

/// 世界坐标 → 画布坐标。y 轴向上：世界 y 越大，画布越靠上。
SchemeScene buildSchemeScene({
  required SchemeLayout before,
  required SchemeLayout after,
  required Size size,
  required double padding,
  required Set<String> changedIds,
  required Set<String> removedIds,
}) {
  final roomWidth = math.max(after.maxX - after.minX, 0.001);
  final roomHeight = math.max(after.maxY - after.minY, 0.001);
  final usableWidth = math.max(size.width - padding * 2, 1.0);
  final usableHeight = math.max(size.height - padding * 2, 1.0);
  final scale = math.min(usableWidth / roomWidth, usableHeight / roomHeight);
  final width = roomWidth * scale;
  final height = roomHeight * scale;
  final left = (size.width - width) / 2;
  final top = (size.height - height) / 2;

  Offset project(double x, double y) =>
      Offset(left + (x - after.minX) * scale, top + (after.maxY - y) * scale);

  final beforeById = {for (final box in before.objects) box.id: box};
  final afterById = {for (final box in after.objects) box.id: box};
  final ids = <String>{...beforeById.keys, ...afterById.keys};
  final boxes = <SchemeBoxScene>[];
  for (final id in ids) {
    final b = beforeById[id];
    final a = afterById[id];
    final removed = removedIds.contains(id) || (b != null && a == null);
    final source = a ?? b!;
    final rotated = (source.rotationDeg % 180).abs() == 90;
    final sizeMeters = rotated ? Size(source.sy, source.sx) : Size(source.sx, source.sy);
    boxes.add(SchemeBoxScene(
      id: id,
      label: source.label,
      assumed: source.assumed,
      changed: changedIds.contains(id),
      removed: removed,
      beforeCenter: b == null ? project(source.cx, source.cy) : project(b.cx, b.cy),
      afterCenter: a == null ? project(source.cx, source.cy) : project(a.cx, a.cy),
      size: Size(sizeMeters.width * scale, sizeMeters.height * scale),
      hidden: removed,
    ));
  }
  return SchemeScene(roomRect: Rect.fromLTWH(left, top, width, height),
      boxes: boxes, metersToPixels: scale);
}

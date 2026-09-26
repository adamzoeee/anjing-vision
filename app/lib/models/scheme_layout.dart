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
    this.doors = const [],
    this.windows = const [],
  });

  final double minX, minY, maxX, maxY;
  final List<SchemeBox> objects;
  final List<SchemeBox> doors;
  final List<SchemeBox> windows;

  factory SchemeLayout.fromJson(Map<String, dynamic> json) {
    final room = (json['room'] as Map?)?.cast<String, dynamic>() ?? const {};
    final bounds = (room['bounds_xy'] as Map?)?.cast<String, dynamic>() ?? const {};
    final min = (bounds['min'] as List? ?? const [0, 0]).map((v) => (v as num).toDouble()).toList();
    final max = (bounds['max'] as List? ?? const [4, 4]).map((v) => (v as num).toDouble()).toList();
    final objects = (json['objects'] as List? ?? const [])
        .whereType<Map>()
        .map((item) => SchemeBox.fromJson(item.cast<String, dynamic>()))
        .toList();
    final doors = (json['doors'] as List? ?? const [])
        .whereType<Map>()
        .map((item) => SchemeBox.fromJson(item.cast<String, dynamic>()))
        .toList();
    final windows = (json['windows'] as List? ?? const [])
        .whereType<Map>()
        .map((item) => SchemeBox.fromJson(item.cast<String, dynamic>()))
        .toList();
    return SchemeLayout(
      minX: min.isNotEmpty ? min[0] : 0, minY: min.length > 1 ? min[1] : 0,
      maxX: max.isNotEmpty ? max[0] : 4, maxY: max.length > 1 ? max[1] : 4,
      objects: objects,
      doors: doors,
      windows: windows,
    );
  }
}

class SchemeBoxScene {
  const SchemeBoxScene({
    required this.id, required this.label, required this.assumed, required this.changed,
    required this.removed, required this.beforeCenter, required this.afterCenter,
    required this.beforeSize, required this.afterSize, required this.hidden,
  });

  final String id;
  final String label;
  final bool assumed;
  final bool changed;
  final bool removed;
  final Offset beforeCenter;
  final Offset afterCenter;
  final Size beforeSize;
  final Size afterSize;
  final bool hidden;
}

class SchemeOpeningScene {
  const SchemeOpeningScene({
    required this.id, required this.kind, required this.changed,
    required this.center, required this.beforeWidthPx, required this.afterWidthPx,
    required this.widthMeters,
  });

  final String id;
  final String kind;
  final bool changed;
  final Offset center;
  final double beforeWidthPx;
  final double afterWidthPx;
  final double widthMeters;
}

class SchemeScene {
  const SchemeScene({
    required this.roomRect,
    required this.boxes,
    required this.doors,
    required this.windows,
    required this.metersToPixels,
  });

  final Rect roomRect;
  final List<SchemeBoxScene> boxes;
  final List<SchemeOpeningScene> doors;
  final List<SchemeOpeningScene> windows;
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

  /// 单个物体在画布上的像素足迹（90°/270° 旋转时交换长宽）。
  Size pixelSizeOf(SchemeBox box) {
    final rotated = (box.rotationDeg % 180).abs() == 90;
    final meters = rotated ? Size(box.sy, box.sx) : Size(box.sx, box.sy);
    return Size(meters.width * scale, meters.height * scale);
  }

  final beforeById = {for (final box in before.objects) box.id: box};
  final afterById = {for (final box in after.objects) box.id: box};
  final ids = <String>{...beforeById.keys, ...afterById.keys};
  final boxes = <SchemeBoxScene>[];
  for (final id in ids) {
    final b = beforeById[id];
    final a = afterById[id];
    final removed = removedIds.contains(id) || (b != null && a == null);
    final source = a ?? b!;
    boxes.add(SchemeBoxScene(
      id: id,
      label: source.label,
      assumed: source.assumed,
      changed: changedIds.contains(id),
      removed: removed,
      beforeCenter: b == null ? project(source.cx, source.cy) : project(b.cx, b.cy),
      afterCenter: a == null ? project(source.cx, source.cy) : project(a.cx, a.cy),
      beforeSize: pixelSizeOf(b ?? source),
      afterSize: pixelSizeOf(a ?? source),
      hidden: removed,
    ));
  }

  /// 门/窗按各自 size[0] 视为洞宽（米）：改造前后宽度差异在叠影图上可见。
  List<SchemeOpeningScene> openingsOf(String kind, List<SchemeBox> beforeItems,
      List<SchemeBox> afterItems) {
    final beforeMap = {for (final box in beforeItems) box.id: box};
    final afterMap = {for (final box in afterItems) box.id: box};
    final keys = <String>{...beforeMap.keys, ...afterMap.keys};
    return keys.map((id) {
      final b = beforeMap[id];
      final a = afterMap[id];
      final source = a ?? b!;
      final beforeWidthPx = (b?.sx ?? source.sx) * scale;
      final afterWidthPx = (a?.sx ?? source.sx) * scale;
      return SchemeOpeningScene(
        id: id,
        kind: kind,
        changed: beforeWidthPx != afterWidthPx,
        center: project(source.cx, source.cy),
        beforeWidthPx: beforeWidthPx,
        afterWidthPx: afterWidthPx,
        widthMeters: a?.sx ?? source.sx,
      );
    }).toList();
  }

  final doors = openingsOf('door', before.doors, after.doors);
  final windows = openingsOf('window', before.windows, after.windows);

  return SchemeScene(roomRect: Rect.fromLTWH(left, top, width, height),
      boxes: boxes, doors: doors, windows: windows, metersToPixels: scale);
}

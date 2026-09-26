import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:anjing_app/models/scheme_layout.dart';
import 'package:anjing_app/widgets/scheme_plan_painter.dart';

/// 只记录文字绘制的画布，用于验证标签确实被画到画布上。
class _RecordingCanvas implements Canvas {
  final List<Paragraph> paragraphs = [];

  @override
  void drawParagraph(Paragraph paragraph, Offset offset) =>
      paragraphs.add(paragraph);

  @override
  void noSuchMethod(Invocation invocation) {}
}

Map<String, dynamic> _layout() => {
      'coordinate_unit': 'meters',
      'z_up': true,
      'room': {
        'bounds_xy': {
          'min': [0.0, 0.0],
          'max': [4.0, 4.0],
        },
        'height_m': 2.7,
      },
      'walls': [],
      'doors': [
        {'id': 'door_01', 'center': [0.6, 0.0, 1.05], 'size': [0.85, 0.12, 2.1], 'rotation_z_deg': 0.0}
      ],
      'windows': [],
      'objects': [
        {'id': 'bed_001', 'label': 'bed', 'center': [1.0, 1.0, 0.3], 'size': [2.0, 1.2, 0.6], 'rotation_z_deg': 0.0},
      ],
    };

void main() {
  test('房间坐标映射到画布时保持比例并且 y 轴向上', () {
    final layout = SchemeLayout.fromJson(_layout());
    final scene = buildSchemeScene(
      before: layout, after: layout, size: const Size(400, 400),
      padding: 20, changedIds: const {}, removedIds: const {},
    );
    expect(scene.roomRect.width, closeTo(360, 0.01));
    expect(scene.roomRect.height, closeTo(360, 0.01));
    final box = scene.boxes.single;
    expect(box.afterCenter.dx, closeTo(110, 0.5));
    expect(box.afterCenter.dy, greaterThan(200)); // 世界 y=1 在房间下部
  });

  test('位移后的盒子同时给出原位与现位矩形', () {
    final before = SchemeLayout.fromJson(_layout());
    final afterJson = _layout();
    ((afterJson['objects'] as List).first as Map)['center'] = [1.0, 2.0, 0.3];
    final after = SchemeLayout.fromJson(afterJson);
    final scene = buildSchemeScene(
      before: before, after: after, size: const Size(400, 400),
      padding: 20, changedIds: const {'bed_001'}, removedIds: const {},
    );
    final box = scene.boxes.single;
    expect(box.changed, isTrue);
    expect(box.beforeCenter.dy - box.afterCenter.dy, closeTo(90, 1.0)); // 1m = 90px
  });

  test('假放置物体被标记为 assumed', () {
    final json = _layout();
    ((json['objects'] as List).first as Map)['assumed'] = true;
    expect(SchemeLayout.fromJson(json).objects.single.assumed, isTrue);
  });

  test('旋转 90° 的物体像素尺寸转置', () {
    final json = _layout();
    final obj = (json['objects'] as List).first as Map;
    obj['size'] = [2.0, 1.2, 0.6];
    obj['rotation_z_deg'] = 90.0;
    final layout = SchemeLayout.fromJson(json);
    final scene = buildSchemeScene(
      before: layout, after: layout, size: const Size(400, 400),
      padding: 20, changedIds: const {}, removedIds: const {},
    );
    final box = scene.boxes.single;
    // 旋转 90° 后世界 1.2m 宽 → 画布宽，2.0m 深 → 画布高（1m = 90px）
    expect(box.afterSize.width, closeTo(1.2 * 90, 0.01));
    expect(box.afterSize.height, closeTo(2.0 * 90, 0.01));
  });

  test('叠影图标签：已移除 / 假设放置 / 普通变更', () {
    final before = SchemeLayout.fromJson(_layout());
    final afterJson = _layout();
    ((afterJson['objects'] as List).first as Map)['assumed'] = true;
    final after = SchemeLayout.fromJson(afterJson);
    final scene = buildSchemeScene(
      before: before, after: after, size: const Size(400, 400),
      padding: 20, changedIds: const {}, removedIds: const {},
    );
    expect(schemeBoxLabel(scene.boxes.single), 'bed · 假设放置');

    final removedScene = buildSchemeScene(
      before: before,
      after: SchemeLayout.fromJson({
        'room': {'bounds_xy': {'min': [0.0, 0.0], 'max': [4.0, 4.0]}},
        'objects': <Map<String, dynamic>>[],
      }),
      size: const Size(400, 400), padding: 20,
      changedIds: const {'bed_001'}, removedIds: const {'bed_001'},
    );
    expect(schemeBoxLabel(removedScene.boxes.single), 'bed · 已移除');

    final movedScene = buildSchemeScene(
      before: before, after: before, size: const Size(400, 400),
      padding: 20, changedIds: const {'bed_001'}, removedIds: const {},
    );
    expect(schemeBoxLabel(movedScene.boxes.single), 'bed');
  });

  testWidgets('叠影图把已移除与假设放置标签画到画布上', (tester) async {
    final before = SchemeLayout.fromJson({
      'room': {'bounds_xy': {'min': [0.0, 0.0], 'max': [4.0, 4.0]}},
      'objects': [
        {'id': 'bed_001', 'label': '床', 'center': [1.0, 1.0, 0.3], 'size': [2.0, 1.2, 0.6]},
        {'id': 'desk_001', 'label': '书桌', 'center': [1.2, 2.0, 0.4], 'size': [1.0, 0.5, 0.8]},
      ],
    });
    final after = SchemeLayout.fromJson({
      'room': {'bounds_xy': {'min': [0.0, 0.0], 'max': [4.0, 4.0]}},
      'objects': [
        {'id': 'bed_001', 'label': '床', 'center': [1.0, 1.0, 0.3], 'size': [2.0, 1.2, 0.6], 'assumed': true},
      ],
    });
    final scene = buildSchemeScene(
      before: before, after: after, size: const Size(400, 400), padding: 16,
      changedIds: const {'desk_001'}, removedIds: const {'desk_001'},
    );
    final canvas = _RecordingCanvas();
    paintSchemePlan(canvas, scene);
    // 床 = 假设放置、书桌 = 已移除，各一条标签；无位移箭头，共 2 条文本。
    expect(canvas.paragraphs.length, 2);
    expect(
      schemeBoxLabel(scene.boxes.firstWhere((box) => box.id == 'bed_001')),
      '床 · 假设放置',
    );
    expect(
      schemeBoxLabel(scene.boxes.firstWhere((box) => box.id == 'desk_001')),
      '书桌 · 已移除',
    );
  });

  test('物体改尺寸后幽灵框保留原始尺寸', () {
    final before = SchemeLayout.fromJson(_layout());
    final afterJson = _layout();
    ((afterJson['objects'] as List).first as Map)['size'] = [1.0, 0.8, 0.6];
    final after = SchemeLayout.fromJson(afterJson);
    final scene = buildSchemeScene(
      before: before, after: after, size: const Size(400, 400),
      padding: 20, changedIds: const {'bed_001'}, removedIds: const {},
    );
    final box = scene.boxes.single;
    expect(box.beforeSize.width, closeTo(2.0 * 90, 0.01));
    expect(box.beforeSize.height, closeTo(1.2 * 90, 0.01));
    expect(box.afterSize.width, closeTo(1.0 * 90, 0.01));
    expect(box.afterSize.height, closeTo(0.8 * 90, 0.01));
  });
}


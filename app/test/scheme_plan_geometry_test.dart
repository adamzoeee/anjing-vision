import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:anjing_app/models/scheme_layout.dart';

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
}


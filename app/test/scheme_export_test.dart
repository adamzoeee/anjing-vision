import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:anjing_app/models/scheme_layout.dart';
import 'package:anjing_app/services/scheme_export.dart';

void main() {
  testWidgets('离屏渲染输出 PNG 字节', (tester) async {
    final layout = SchemeLayout.fromJson({
      'room': {
        'bounds_xy': {
          'min': [0.0, 0.0],
          'max': [4.0, 4.0],
        },
      },
      'objects': [
        {'id': 'bed_001', 'label': '床', 'center': [1.0, 3.0, 0.3], 'size': [2.0, 1.2, 0.6], 'rotation_z_deg': 0.0}
      ],
    });
    final scene = buildSchemeScene(
      before: layout, after: layout, size: const Size(360, 360), padding: 16,
      changedIds: const {}, removedIds: const {},
    );
    // Picture.toImage 是引擎层的真实异步操作，需在 runAsync 中执行，
    // 否则 testWidgets 的 fake-async 区域不会推进该 Future。
    final bytes = await tester.runAsync(() => renderSchemePng(scene));
    expect(bytes, isNotNull);
    expect(bytes!.length, greaterThan(1000));
    expect(bytes.sublist(0, 8), [137, 80, 78, 71, 13, 10, 26, 10]); // PNG 签名
  });
}

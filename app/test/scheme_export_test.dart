import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:anjing_app/models/scheme_layout.dart';
import 'package:anjing_app/services/scheme_export.dart';

/// 只记录文字绘制的画布，用于验证声明页脚确实被画到画布上。
class _RecordingCanvas implements Canvas {
  final List<({Paragraph paragraph, Offset offset})> records = [];

  @override
  void drawParagraph(Paragraph paragraph, Offset offset) =>
      records.add((paragraph: paragraph, offset: offset));

  @override
  void noSuchMethod(Invocation invocation) {}
}

void main() {
  test('导出声明标签与页脚文案同源', () {
    expect(kSchemeDisclaimerLabel, '沙盒模拟 · 非实测');
    expect(schemeFooterText(), kSchemeDisclaimerLabel);
    expect(schemeFooterText(' 方案A '), '沙盒模拟 · 非实测 · 方案A');
    expect(schemeFooterText('   '), kSchemeDisclaimerLabel);
  });

  test('导出文件名替换 Windows 非法字符并裁剪', () {
    expect(sanitizeSchemeFileName('改造方案 09-26 18:20'), '改造方案 09-26 18-20');
    expect(sanitizeSchemeFileName('a/b\\c:d*e?f"g<h>i|j'), 'a-b-c-d-e-f-g-h-i-j');
    expect(sanitizeSchemeFileName('   '), 'scheme');
  });

  testWidgets('页脚用统一声明文案并画在画布左下角', (tester) async {
    final footer = schemeFooterText('方案A');
    expect(
      schemeFooterPainter(footer, size: 360).text!.toPlainText(),
      '沙盒模拟 · 非实测 · 方案A',
    );

    final canvas = _RecordingCanvas();
    paintSchemeFooter(canvas, footer, size: 360);
    expect(canvas.records.length, 1);
    final record = canvas.records.single;
    expect(record.paragraph.height, greaterThan(0));
    expect(record.offset.dx, lessThan(180));
    expect(record.offset.dy, greaterThan(180));
  });

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
    final bytes = await tester.runAsync(
      () => renderSchemePng(scene, footer: schemeFooterText('方案A')),
    );
    expect(bytes, isNotNull);
    expect(bytes!.length, greaterThan(1000));
    expect(bytes.sublist(0, 8), [137, 80, 78, 71, 13, 10, 26, 10]); // PNG 签名
  });
}

import 'package:anjing_app/api/models.dart';
import 'package:anjing_app/pages/capture_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('采集页要求参考尺寸且不再要求 A4 纸', (tester) async {
    final project = Project(id: 1, name: '测试房间');
    final scan = Scan(
      id: 2,
      projectId: 1,
      status: 'uploading',
      progress: 0,
      message: '',
      captureType: 'video',
    );

    await tester.pumpWidget(
      MaterialApp(
        home: CapturePage(project: project, scan: scan),
      ),
    );

    expect(find.text('先填写 2～3 个真实尺寸'), findsOneWidget);
    expect(find.textContaining('无需放置 A4 纸'), findsOneWidget);
    expect(find.text('增加第 3 个参考尺寸'), findsOneWidget);
    expect(find.text('保存尺寸并开始录制', skipOffstage: false), findsOneWidget);
    expect(find.text('保存尺寸并选择已有视频', skipOffstage: false), findsOneWidget);
    expect(find.textContaining('找一张 A4'), findsNothing);

    await tester.tap(find.text('增加第 3 个参考尺寸'));
    await tester.pump();
    expect(find.text('增加第 3 个参考尺寸'), findsNothing);
    expect(find.byType(TextField), findsNWidgets(3));
  });
}

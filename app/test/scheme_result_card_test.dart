import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:anjing_app/widgets/scheme_plan_painter.dart';
import 'package:anjing_app/widgets/scheme_result_card.dart';

Map<String, dynamic> _layout(double deskY) => {
      'room': {
        'bounds_xy': {
          'min': [0.0, 0.0],
          'max': [4.0, 4.0],
        },
      },
      'objects': [
        {'id': 'bed_001', 'label': '床', 'center': [1.0, 3.0, 0.3], 'size': [2.0, 1.2, 0.6], 'rotation_z_deg': 0.0},
        {'id': 'desk_001', 'label': '书桌', 'center': [1.2, deskY, 0.4], 'size': [1.0, 0.5, 0.8], 'rotation_z_deg': 0.0},
      ],
    };

Map<String, dynamic> _payload() => {
      'layout_before': _layout(2.0),
      'layout_after': _layout(1.0),
      'changed_ids': ['desk_001'],
      'removed_ids': <String>[],
      'assumed_ids': <String>[],
      'conflicts': <Map<String, dynamic>>[],
      'comparison': {'before_score': 80.0, 'after_score': 85.0, 'score_delta': 5.0},
      'metric_changes': [
        {'metric_code': 'bedside_clearance', 'name': '床边活动空间', 'unit': 'm', 'before': 0.30, 'after': 0.55},
      ],
    };

void main() {
  testWidgets('方案结果卡展示分数、叠影图与指标变化', (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: SingleChildScrollView(child: SchemeResultCard(payload: _payload()))),
    ));
    expect(find.text('80.0 → 85.0'), findsOneWidget);
    expect(find.text('沙盒模拟 · 非实测'), findsOneWidget);
    expect(find.textContaining('床边活动空间'), findsOneWidget);
    expect(find.byType(CustomPaint), findsWidgets);
    expect(
      find.byWidgetPredicate(
        (widget) => widget is CustomPaint && widget.painter is SchemePlanPainter,
      ),
      findsOneWidget,
    );
  });

  testWidgets('缺少布局数据时显示文字回退而不崩溃', (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: SchemeResultCard(payload: {
            'comparison': {
              'before_score': 70.0,
              'after_score': 72.5,
              'score_delta': 2.5,
            },
            'metric_changes': <Map<String, dynamic>>[],
          }),
        ),
      ),
    ));
    expect(find.text('70.0 → 72.5'), findsOneWidget);
    expect(find.textContaining('平面'), findsOneWidget);
    expect(
      find.byWidgetPredicate(
        (widget) => widget is CustomPaint && widget.painter is SchemePlanPainter,
      ),
      findsNothing,
    );
    expect(find.text('沙盒模拟 · 非实测'), findsOneWidget);
  });

  testWidgets('缺少 comparison 时显示无法评分', (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: SchemeResultCard(payload: {
            'layout_before': _layout(2.0),
            'layout_after': _layout(1.0),
          }),
        ),
      ),
    ));
    expect(find.text('无法评分'), findsOneWidget);
  });

  testWidgets('假设放置提示来自布局对象而非 assumed_ids', (tester) async {
    final after = _layout(1.0);
    ((after['objects'] as List)[1] as Map)['assumed'] = true;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: SchemeResultCard(payload: {
            'layout_before': _layout(2.0),
            'layout_after': after,
            'changed_ids': ['desk_001'],
            'assumed_ids': <String>[],
            'comparison': {'before_score': 80.0, 'after_score': 85.0},
          }),
        ),
      ),
    ));
    expect(find.text('含假设放置物体 · 非实测'), findsOneWidget);
  });

  testWidgets('显示需施工提示当 requiresConstruction 为真', (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: SchemeResultCard(
            payload: _payload(),
            requiresConstruction: true,
          ),
        ),
      ),
    ));
    expect(find.text('需施工：改门洞/换门'), findsOneWidget);
  });

  testWidgets('渲染风险等级变化 before → after', (tester) async {
    final payload = _payload()
      ..['risk_changes'] = [
        {'name': '门宽风险', 'before': 'medium', 'after': 'low'},
      ];
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(child: SchemeResultCard(payload: payload)),
      ),
    ));
    expect(find.textContaining('门宽风险'), findsOneWidget);
    expect(find.textContaining('中 → 低'), findsOneWidget);
  });

  testWidgets('非重叠冲突按类型显示正确文案', (tester) async {
    final payload = _payload()
      ..['conflicts'] = [
        {'type': 'out_of_room', 'target': 'desk_001', 'op': 'move'},
        {'type': 'overlap', 'target': 'desk_001', 'other': 'bed_001'},
        {'type': 'invalid_door_width', 'width_m': 5.0},
      ];
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(child: SchemeResultCard(payload: payload)),
      ),
    ));
    expect(find.text('目标位置超出房间边界，已拒绝'), findsOneWidget);
    expect(find.text('该位置与 bed_001 重叠，已拒绝'), findsOneWidget);
    expect(find.text('门洞宽度超出合理范围（0.5–1.5m），已拒绝'), findsOneWidget);
  });
}

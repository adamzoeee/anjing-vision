import 'package:anjing_app/api/models.dart';
import 'package:anjing_app/widgets/risk_card.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('正式高风险卡显示测量、置信度和后端建议', (tester) async {
    final risk = Risk.fromJson({
      'risk_code': 'door_width_high',
      'risk_type': 'mobility',
      'risk_name': '门净宽风险',
      'metric_code': 'door_width',
      'measured_value': 0.75,
      'unit': 'm',
      'risk_level': 'high',
      'confidence': 0.9,
      'advice': '评估扩宽门洞。',
      'assessment_status': 'evaluated',
    });
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: RiskCard(risk: risk))));
    expect(find.text('门净宽风险'), findsOneWidget);
    expect(find.text('高风险'), findsOneWidget);
    expect(find.textContaining('测量值：0.75 m'), findsOneWidget);
    expect(find.textContaining('置信度：90%'), findsOneWidget);
    expect(find.textContaining('建议：评估扩宽门洞。'), findsOneWidget);
    expect(find.byIcon(Icons.dangerous), findsOneWidget);
  });

  testWidgets('不可评估卡不显示成低风险', (tester) async {
    final risk = Risk.fromJson({
      'risk_code': 'activity_area_not_evaluable',
      'risk_name': '活动区域面积风险',
      'metric_code': 'activity_area',
      'risk_level': null,
      'assessment_status': 'not_evaluable',
      'reason': 'explicit_activity_anchor_missing',
    });
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: RiskCard(risk: risk))));
    expect(find.text('无法评估'), findsOneWidget);
    expect(find.textContaining('explicit_activity_anchor_missing'), findsOneWidget);
    expect(find.byIcon(Icons.help_outline), findsOneWidget);
    expect(find.byIcon(Icons.check_circle), findsNothing);
  });
}

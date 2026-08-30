import 'package:anjing_app/api/models.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('模型 JSON 解析', () {
    test('Scan.fromJson 解析状态字段', () {
      final s = Scan.fromJson({
        'id': 1,
        'project_id': 2,
        'status': 'training',
        'progress': 45,
        'message': '3D 重建训练中',
        'capture_type': 'video',
      });
      expect(s.id, 1);
      expect(s.projectId, 2);
      expect(s.status, 'training');
      expect(s.progress, 45);
      expect(s.message, '3D 重建训练中');
    });

    test('Scan.done/failed 判定', () {
      expect(
        Scan.fromJson({'id': 1, 'project_id': 2, 'status': 'done'}).done,
        true,
      );
      expect(
        Scan.fromJson({'id': 1, 'project_id': 2, 'status': 'failed'}).failed,
        true,
      );
      expect(
        Scan.fromJson({'id': 1, 'project_id': 2, 'status': 'sfm'}).done,
        false,
      );
    });

    test('Report.fromJson 解析风险与评分', () {
      final r = Report.fromJson({
        'scan_id': 1,
        'score': 62.5,
        'risks': [
          {'code': 'door_width', 'name': '门宽', 'level': 'red', 'measure': 0.75},
        ],
        'advice': ['建议扩门'],
        'images': [],
        'calibrated': 1,
        'measures': {
          'reference_measurements': [
            {'object_type': 'bed', 'dimension': 'width', 'meters': 1.5},
          ],
        },
        'preview': {
          'ply': 'scene.ply',
          'gaussian_ply': 'scene_gaussian.ply',
          'cameras': 'cameras.json',
        },
      });
      expect(r.score, 62.5);
      expect(r.risks.first.level, 'red');
      expect(r.risks.first.measure, 0.75);
      expect(r.advice, ['建议扩门']);
      expect(r.calibrated, 1);
      expect(r.previewPly, 'scene.ply');
      expect(r.previewGaussianPly, 'scene_gaussian.ply');
      expect(r.previewCameras, 'cameras.json');
      expect(r.measures['reference_measurements'].first['dimension'], 'width');
    });

    test('Report.fromJson 解析正式风险评估字段', () {
      final r = Report.fromJson({
        'scan_id': 46,
        'score': 73.2,
        'risks': [
          {
            'risk_code': 'door_width_medium',
            'risk_type': 'mobility',
            'risk_name': '门净宽风险',
            'metric_code': 'door_width',
            'measured_value': 0.85,
            'unit': 'm',
            'threshold': {'direction': 'below', 'levels': {'medium': 0.9}},
            'position': {'object_id': 'door_01'},
            'risk_level': 'medium',
            'confidence': 0.9,
            'reason': 'threshold',
            'advice': '调整门口净宽',
            'assessment_status': 'evaluated',
            'related_object_ids': ['door_01'],
            'related_path_id': null,
          },
        ],
        'advice': ['调整门口净宽'],
        'images': [],
        'calibrated': 3,
        'measures': {
          'risk_assessment': {
            'official': true,
            'overall': {'score': 73.2, 'coverage_percent': 86.7},
          },
        },
      });
      final risk = r.risks.single;
      expect(risk.code, 'door_width_medium');
      expect(risk.metricCode, 'door_width');
      expect(risk.riskType, 'mobility');
      expect(risk.level, 'medium');
      expect(risk.measure, 0.85);
      expect(risk.confidence, 0.9);
      expect(risk.relatedObjectIds, ['door_01']);
      expect(r.riskAssessment['official'], true);
    });

    test('Project.fromJson 默认 address 空串', () {
      final p = Project.fromJson({'id': 1, 'name': '王奶奶家'});
      expect(p.address, '');
    });
  });
}

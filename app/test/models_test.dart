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
    });

    test('Project.fromJson 默认 address 空串', () {
      final p = Project.fromJson({'id': 1, 'name': '王奶奶家'});
      expect(p.address, '');
    });
  });
}

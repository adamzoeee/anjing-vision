class AuthUser {
  final int id;
  final String name;
  final String email;
  final String role;
  final String orgName;
  AuthUser({
    required this.id,
    required this.name,
    required this.email,
    required this.role,
    required this.orgName,
  });
  factory AuthUser.fromJson(Map<String, dynamic> j) => AuthUser(
    id: j['id'],
    name: j['name'],
    email: j['email'],
    role: j['role'],
    orgName: j['org_name'],
  );
}

class Project {
  final int id;
  final String name;
  final String address;
  Project({required this.id, required this.name, this.address = ''});
  factory Project.fromJson(Map<String, dynamic> j) =>
      Project(id: j['id'], name: j['name'], address: j['address'] ?? '');
}

class Scan {
  final int id;
  final int projectId;
  final String status;
  final int progress;
  final String message;
  final String captureType;
  Scan({
    required this.id,
    required this.projectId,
    required this.status,
    required this.progress,
    required this.message,
    required this.captureType,
  });
  factory Scan.fromJson(Map<String, dynamic> j) => Scan(
    id: j['id'],
    projectId: j['project_id'],
    status: j['status'],
    progress: j['progress'] ?? 0,
    message: j['message'] ?? '',
    captureType: j['capture_type'] ?? '',
  );
  bool get done => status == 'done';
  bool get failed => status == 'failed';
}

class Risk {
  final String code;
  final String name;
  final String level;
  final dynamic measure;
  final String? metricCode;
  final String? riskType;
  final String unit;
  final Map<String, dynamic>? threshold;
  final dynamic position;
  final double? confidence;
  final String? advice;
  final List<String> relatedObjectIds;
  final String? relatedPathId;
  final String assessmentStatus;
  final String? reason;
  Risk({
    required this.code,
    required this.name,
    required this.level,
    this.measure,
    this.metricCode,
    this.riskType,
    this.unit = '',
    this.threshold,
    this.position,
    this.confidence,
    this.advice,
    this.relatedObjectIds = const [],
    this.relatedPathId,
    this.assessmentStatus = 'not_evaluable',
    this.reason,
  });
  factory Risk.fromJson(Map<String, dynamic> j) => Risk(
    code: (j['risk_code'] ?? j['code'] ?? '').toString(),
    name: (j['risk_name'] ?? j['name'] ?? '').toString(),
    level: (j['risk_level'] ?? j['level'] ?? 'unknown').toString(),
    measure: j.containsKey('measured_value') ? j['measured_value'] : j['measure'],
    metricCode: j['metric_code']?.toString(),
    riskType: j['risk_type']?.toString(),
    unit: j['unit']?.toString() ?? '',
    threshold: j['threshold'] is Map
        ? Map<String, dynamic>.from(j['threshold'] as Map)
        : null,
    position: j['position'],
    confidence: (j['confidence'] as num?)?.toDouble(),
    advice: j['advice']?.toString(),
    relatedObjectIds: (j['related_object_ids'] as List? ?? const [])
        .map((value) => value.toString())
        .toList(),
    relatedPathId: j['related_path_id']?.toString(),
    assessmentStatus: j['assessment_status']?.toString() ??
        (j['level'] == 'green'
            ? 'evaluated_safe'
            : j['level'] == 'red' || j['level'] == 'yellow'
                ? 'evaluated_risk'
                : 'not_evaluable'),
    reason: j['reason']?.toString(),
  );
}

class Report {
  final int scanId;
  final double? score;
  final List<Risk> risks;
  final List<String> advice;
  final List<String> images;
  final int calibrated;
  final String? previewPly;
  final String? previewGaussianPly;
  final String? previewCameras;
  final String? previewViewer;
  final String? reportPdf;
  final String? riskMap;
  final Map<String, dynamic> measures;
  final Map<String, dynamic> riskAssessment;
  Report({
    required this.scanId,
    required this.score,
    required this.risks,
    required this.advice,
    required this.images,
    required this.calibrated,
    this.previewPly,
    this.previewGaussianPly,
    this.previewCameras,
    this.previewViewer,
    this.reportPdf,
    this.riskMap,
    this.measures = const {},
    this.riskAssessment = const {},
  });
  factory Report.fromJson(Map<String, dynamic> j) {
    final measures = Map<String, dynamic>.from(j['measures'] as Map? ?? const {});
    final assessment = Map<String, dynamic>.from(
      measures['risk_assessment'] as Map? ?? const {},
    );
    return Report(
      scanId: j['scan_id'],
      score: (j['score'] as num?)?.toDouble(),
      risks: (j['risks'] as List? ?? []).map((e) => Risk.fromJson(e)).toList(),
      advice: (j['advice'] as List? ?? []).map((e) => e.toString()).toList(),
      images: (j['images'] as List? ?? []).map((e) => e.toString()).toList(),
      calibrated: j['calibrated'] ?? 0,
      previewPly: (j['preview'] as Map<String, dynamic>?)?['ply']?.toString(),
      previewGaussianPly: (j['preview'] as Map<String, dynamic>?)?['gaussian_ply']
          ?.toString(),
      previewCameras: (j['preview'] as Map<String, dynamic>?)?['cameras']
          ?.toString(),
      previewViewer: (j['preview'] as Map<String, dynamic>?)?['viewer']?.toString(),
      reportPdf: (j['preview'] as Map<String, dynamic>?)?['pdf']?.toString(),
      riskMap: (j['preview'] as Map<String, dynamic>?)?['risk_map']?.toString(),
      measures: measures,
      riskAssessment: assessment,
    );
  }
}

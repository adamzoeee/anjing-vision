# 安龄智境：Flutter App 实现计划

> **面向 AI 代理的工作者：** 必需技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 实现跨平台采集端 App：注册/登录 → 项目列表 → 录制房间视频（或拍照片）→ 上传 → 实时进度 → 查看评估报告（评分/风险列表/标注图/交互式 3D 预览）→ 改造前后对比。

**架构：** 依赖计划 A（后端 API 契约，见 `docs/superpowers/plans/2026-07-31-后端与重建管道.md` 任务 10/11）。`lib/api/` 封装 REST + JWT；`lib/pages/` 为页面；3D 预览用 `webview_flutter` 加载 `web/preview/index.html`（内嵌 antimatter15/splat WebGL 渲染器，加载后端返回的 scene.ply）。

**技术栈：** Flutter 3.x、dio（HTTP）、provider（状态）、webview_flutter、camera / image_picker / video_player、web_socket_channel（进度推送，可选——轮询兜底）。

---

## 文件结构

```
app/
├── pubspec.yaml
├── lib/
│   ├── main.dart                    # 入口 + Provider 装配
│   ├── api/
│   │   ├── client.dart              # dio + JWT 拦截器 + 错误处理
│   │   └── models.dart              # AuthUser/Project/Scan/Report 模型
│   ├── state/
│   │   └── auth_store.dart          # 登录态 + token 持久化（shared_preferences）
│   ├── pages/
│   │   ├── login_page.dart          # 注册/登录
│   │   ├── projects_page.dart       # 项目列表 + 新建
│   │   ├── project_detail_page.dart # 项目下的扫描记录 + 对比入口
│   │   ├── capture_page.dart        # 视频录制引导（提示文案 + 录制约束）
│   │   ├── upload_page.dart         # 上传 + 进度条（轮询 scan.status）
│   │   └── report_page.dart         # 评分/风险列表/标注图/3D 预览
│   └── widgets/
│       ├── risk_card.dart           # 单条风险展示
│       └── score_gauge.dart         # 评分环
└── web/
    └── preview/
        ├── index.html               # WebGL 高斯渲染器页面
        └── splat.js                 # antimatter15/splat 单文件渲染器
```

---

## 任务 1：安装 Flutter 并创建项目

**文件：**
- 创建：`app/`（flutter create 产物）
- 修改：无

- [ ] **步骤 1：下载安装 Flutter SDK（Windows）**

```bash
# 从 https://docs.flutter.dev/get-started/install/windows 下载 stable zip（约 1GB）
# 解压到 D:\flutter（示例），并把 bin 加入 PATH：
export PATH="$PATH:/d/flutter/bin"
flutter --version
```

预期：打印 Flutter 3.x 版本。

- [ ] **步骤 2：创建项目**

```bash
cd "E:\.PJs\old-friend"
flutter create --org com.anjing --project-name anjing_app --platforms android,ios app
```

- [ ] **步骤 3：添加依赖**

`app/pubspec.yaml`（dependencies 段追加）：

```yaml
  dio: ^5.4.0
  provider: ^6.1.1
  shared_preferences: ^2.2.2
  image_picker: ^1.0.5
  video_player: ^2.8.2
  webview_flutter: ^4.7.0
  intl: ^0.19.0
```

```bash
cd app && flutter pub get
```

- [ ] **步骤 4：Commit**

```bash
git add app && git commit -m "chore: Flutter 项目骨架与依赖"
```

---

## 任务 2：API 客户端与数据模型

**文件：**
- 创建：`lib/api/client.dart`、`lib/api/models.dart`、`test/api_client_test.dart`
- 修改：无

- [ ] **步骤 1：编写失败测试**

`test/api_client_test.dart`：

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:anjing_app/api/models.dart';

void main() {
  test('Scan.fromJson 解析状态字段', () {
    final s = Scan.fromJson({'id': 1, 'project_id': 2, 'status': 'training',
      'progress': 45, 'message': '3D 重建训练中', 'capture_type': 'video'});
    expect(s.id, 1);
    expect(s.status, 'training');
    expect(s.progress, 45);
  });

  test('Report.fromJson 解析风险与评分', () {
    final r = Report.fromJson({'scan_id': 1, 'score': 62.5,
      'risks': [{'code': 'door_width', 'name': '门宽', 'level': 'red', 'measure': 0.75}],
      'advice': ['建议扩门'], 'calibrated': 1});
    expect(r.score, 62.5);
    expect(r.risks.first.level, 'red');
    expect(r.advice, ['建议扩门']);
  });
}
```

- [ ] **步骤 2：运行测试确认失败**

```bash
cd app && flutter test test/api_client_test.dart
```

预期：FAIL，`Scan`/`Report` 未定义。

- [ ] **步骤 3：实现模型**

`lib/api/models.dart`：

```dart
class AuthUser {
  final int id; final String name; final String email; final String role; final String orgName;
  AuthUser({required this.id, required this.name, required this.email, required this.role, required this.orgName});
  factory AuthUser.fromJson(Map<String, dynamic> j) => AuthUser(
    id: j['id'], name: j['name'], email: j['email'], role: j['role'], orgName: j['org_name']);
}

class Project {
  final int id; final String name; final String address;
  Project({required this.id, required this.name, this.address = ''});
  factory Project.fromJson(Map<String, dynamic> j) =>
      Project(id: j['id'], name: j['name'], address: j['address'] ?? '');
}

class Scan {
  final int id; final int projectId; final String status; final int progress;
  final String message; final String captureType;
  Scan({required this.id, required this.projectId, required this.status,
    required this.progress, required this.message, required this.captureType});
  factory Scan.fromJson(Map<String, dynamic> j) => Scan(
    id: j['id'], projectId: j['project_id'], status: j['status'],
    progress: j['progress'] ?? 0, message: j['message'] ?? '', captureType: j['capture_type']);
  bool get done => status == 'done';
  bool get failed => status == 'failed';
}

class Risk {
  final String code; final String name; final String level; final dynamic measure;
  Risk({required this.code, required this.name, required this.level, this.measure});
  factory Risk.fromJson(Map<String, dynamic> j) =>
      Risk(code: j['code'], name: j['name'], level: j['level'], measure: j['measure']);
}

class Report {
  final int scanId; final double score; final List<Risk> risks;
  final List<String> advice; final List<String> images; final int calibrated;
  Report({required this.scanId, required this.score, required this.risks,
    required this.advice, required this.images, required this.calibrated});
  factory Report.fromJson(Map<String, dynamic> j) => Report(
    scanId: j['scan_id'], score: (j['score'] ?? 0).toDouble(),
    risks: (j['risks'] as List? ?? []).map((e) => Risk.fromJson(e)).toList(),
    advice: (j['advice'] as List? ?? []).map((e) => e.toString()).toList(),
    images: (j['images'] as List? ?? []).map((e) => e.toString()).toList(),
    calibrated: j['calibrated'] ?? 0);
}
```

- [ ] **步骤 4：实现客户端**

`lib/api/client.dart`：

```dart
import 'package:dio/dio.dart';
import 'models.dart';

class ApiClient {
  final Dio dio;
  String? _token;
  ApiClient({String? baseUrl})
      : dio = Dio(BaseOptions(
          baseUrl: baseUrl ?? 'http://10.0.2.2:8000', // Android 模拟器访问宿主机
          connectTimeout: const Duration(seconds: 15),
          receiveTimeout: const Duration(seconds: 60))) {
    dio.interceptors.add(InterceptorsWrapper(onRequest: (o, h) {
      if (_token != null) o.headers['Authorization'] = 'Bearer $_token';
      h.next(o);
    }));
  }

  void setToken(String? t) => _token = t;

  Future<({String token, AuthUser user})> register(
      {required String orgName, required String name,
       required String email, required String password}) async {
    final r = await dio.post('/api/auth/register', data: {
      'org_name': orgName, 'name': name, 'email': email, 'password': password});
    _token = r.data['token'];
    return (token: _token!, user: _user(r.data['user']));
  }

  Future<({String token, AuthUser user})> login(
      {required String email, required String password}) async {
    final r = await dio.post('/api/auth/login',
        data: {'email': email, 'password': password});
    _token = r.data['token'];
    return (token: _token!, user: _user(r.data['user']));
  }

  AuthUser _user(Map<String, dynamic> j) => AuthUser.fromJson(j);

  Future<List<Project>> projects() async =>
      (await dio.get('/api/projects')).data
          .map<Project>((e) => Project.fromJson(e)).toList();

  Future<Project> createProject(String name, String address) async =>
      Project.fromJson((await dio.post('/api/projects',
          data: {'name': name, 'address': address})).data);

  Future<Scan> createScan(int projectId, String captureType) async =>
      Scan.fromJson((await dio.post('/api/projects/$projectId/scans',
          data: {'capture_type': captureType})).data);

  Future<void> uploadVideo(int scanId, String path, String filename) async {
    final bytes = await Dio().get<List<int>>(path, options: Options(responseType: ResponseType.bytes));
    await dio.post('/api/scans/$scanId/upload',
        data: Stream.fromIterable([bytes.data!]),
        options: Options(headers: {'Content-Type': 'video/mp4'},
            contentType: 'application/octet-stream'));
  }

  Future<Scan> scanStatus(int scanId) async =>
      Scan.fromJson((await dio.get('/api/scans/$scanId')).data);

  Future<Report> report(int scanId) async =>
      Report.fromJson((await dio.get('/api/reports/scans/$scanId')).data);

  Future<Map<String, dynamic>> compare(int a, int b) async =>
      (await dio.get('/api/reports/compare', queryParameters: {'a': a, 'b': b})).data;
}
```

- [ ] **步骤 5：运行测试确认通过**

```bash
cd app && flutter test test/api_client_test.dart
```

预期：PASS（2 passed）。

- [ ] **步骤 6：Commit**

```bash
git add app && git commit -m "feat: API 客户端与数据模型"
```

---

## 任务 3：登录态与登录/注册页

**文件：**
- 创建：`lib/state/auth_store.dart`、`lib/pages/login_page.dart`、`lib/main.dart`
- 修改：无

- [ ] **步骤 1：实现 AuthStore**

`lib/state/auth_store.dart`：

```dart
import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../api/client.dart';
import '../api/models.dart';

class AuthStore extends ChangeNotifier {
  final ApiClient api;
  AuthStore(this.api);
  AuthUser? user;
  bool loading = false;
  String? error;

  Future<bool> login(String email, String password) async {
    loading = true; error = null; notifyListeners();
    try {
      final r = await api.login(email: email, password: password);
      user = r.user;
      await _persist(r.token);
      return true;
    } catch (e) {
      error = '登录失败: $e'; return false;
    } finally {
      loading = false; notifyListeners();
    }
  }

  Future<bool> register(String org, String name, String email, String password) async {
    loading = true; error = null; notifyListeners();
    try {
      final r = await api.register(orgName: org, name: name, email: email, password: password);
      user = r.user;
      await _persist(r.token);
      return true;
    } catch (e) {
      error = '注册失败: $e'; return false;
    } finally {
      loading = false; notifyListeners();
    }
  }

  Future<void> _persist(String token) async {
    final p = await SharedPreferences.getInstance();
    await p.setString('token', token);
    api.setToken(token);
  }

  Future<void> restore() async {
    final p = await SharedPreferences.getInstance();
    final t = p.getString('token');
    if (t != null) api.setToken(t);
  }

  void logout() async {
    user = null;
    (await SharedPreferences.getInstance()).remove('token');
    api.setToken(null);
    notifyListeners();
  }
}
```

- [ ] **步骤 2：实现登录页**

`lib/pages/login_page.dart`：

```dart
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../state/auth_store.dart';

class LoginPage extends StatefulWidget {
  const LoginPage({super.key});
  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final _email = TextEditingController();
  final _pw = TextEditingController();
  bool _register = false;
  final _org = TextEditingController();
  final _name = TextEditingController();

  Future<void> _submit() async {
    final store = context.read<AuthStore>();
    final ok = _register
        ? await store.register(_org.text, _name.text, _email.text, _pw.text)
        : await store.login(_email.text, _pw.text);
    if (ok && mounted) Navigator.of(context).pushReplacementNamed('/projects');
  }

  @override
  Widget build(BuildContext context) {
    final store = context.watch<AuthStore>();
    return Scaffold(
      appBar: AppBar(title: const Text('安龄智境')),
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(children: [
          TextField(controller: _email, decoration: const InputDecoration(labelText: '邮箱')),
          TextField(controller: _pw, obscureText: true, decoration: const InputDecoration(labelText: '密码')),
          if (_register) ...[
            TextField(controller: _org, decoration: const InputDecoration(labelText: '机构名称')),
            TextField(controller: _name, decoration: const InputDecoration(labelText: '姓名')),
          ],
          if (store.error != null) Text(store.error!, style: const TextStyle(color: Colors.red)),
          ElevatedButton(
            onPressed: store.loading ? null : _submit,
            child: Text(_register ? '注册并登录' : '登录')),
          TextButton(
            onPressed: () => setState(() => _register = !_register),
            child: Text(_register ? '已有账号？去登录' : '没有账号？注册')),
        ]),
      ),
    );
  }
}
```

- [ ] **步骤 3：实现入口**

`lib/main.dart`：

```dart
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'api/client.dart';
import 'pages/login_page.dart';
import 'pages/projects_page.dart';
import 'state/auth_store.dart';

void main() {
  runApp(const AnjingApp());
}

class AnjingApp extends StatelessWidget {
  const AnjingApp({super.key});
  @override
  Widget build(BuildContext context) {
    final api = ApiClient();
    return ChangeNotifierProvider(
      create: (_) => AuthStore(api)..restore(),
      child: MaterialApp(
        title: '安龄智境',
        theme: ThemeData(colorSchemeSeed: Colors.teal, useMaterial3: true),
        initialRoute: '/login',
        routes: {
          '/login': (_) => const LoginPage(),
          '/projects': (_) => const ProjectsPage(),
        },
      ),
    );
  }
}
```

- [ ] **步骤 4：静态检查**

```bash
cd app && flutter analyze
```

预期：No issues found。

- [ ] **步骤 5：Commit**

```bash
git add app && git commit -m "feat: 登录态管理、登录/注册页与入口"
```

---

## 任务 4：项目列表与详情页

**文件：**
- 创建：`lib/pages/projects_page.dart`、`lib/pages/project_detail_page.dart`
- 修改：无

- [ ] **步骤 1：实现项目列表页**

`lib/pages/projects_page.dart`：

```dart
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../api/client.dart';
import '../api/models.dart';
import '../state/auth_store.dart';
import 'project_detail_page.dart';

class ProjectsPage extends StatefulWidget {
  const ProjectsPage({super.key});
  @override
  State<ProjectsPage> createState() => _ProjectsPageState();
}

class _ProjectsPageState extends State<ProjectsPage> {
  List<Project>? _projects;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final list = await context.read<ApiClient>().projects();
      setState(() { _projects = list; _error = null; });
    } catch (e) {
      setState(() => _error = '$e');
    }
  }

  Future<void> _create() async {
    final name = await showDialog<String>(context: context, builder: (c) {
      final tc = TextEditingController();
      return AlertDialog(
        title: const Text('新建评估项目'),
        content: TextField(controller: tc, decoration: const InputDecoration(labelText: '项目名（如：王奶奶家）')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c), child: const Text('取消')),
          TextButton(onPressed: () => Navigator.pop(c, tc.text), child: const Text('创建')),
        ]);
    });
    if (name != null && name.isNotEmpty) {
      await context.read<ApiClient>().createProject(name, '');
      _load();
    }
  }

  @override
  Widget build(BuildContext context) {
    final store = context.watch<AuthStore>();
    return Scaffold(
      appBar: AppBar(
        title: Text('项目列表（${store.user?.orgName ?? ''}）'),
        actions: [IconButton(onPressed: store.logout, icon: const Icon(Icons.logout))]),
      floatingActionButton: FloatingActionButton(onPressed: _create, child: const Icon(Icons.add)),
      body: _error != null
          ? Center(child: Text(_error!))
          : _projects == null
              ? const Center(child: CircularProgressIndicator())
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView.builder(
                    itemCount: _projects!.length,
                    itemBuilder: (_, i) {
                      final p = _projects![i];
                      return ListTile(
                        title: Text(p.name),
                        subtitle: Text(p.address.isEmpty ? '点击进入' : p.address),
                        trailing: const Icon(Icons.chevron_right),
                        onTap: () => Navigator.push(context,
                            MaterialPageRoute(builder: (_) => ProjectDetailPage(project: p))),
                      );
                    })),
    );
  }
}
```

- [ ] **步骤 2：实现项目详情页（扫描记录列表 + 新建扫描入口 + 对比入口）**

`lib/pages/project_detail_page.dart`：

```dart
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../api/client.dart';
import '../api/models.dart';
import 'capture_page.dart';
import 'report_page.dart';

class ProjectDetailPage extends StatefulWidget {
  final Project project;
  const ProjectDetailPage({super.key, required this.project});
  @override
  State<ProjectDetailPage> createState() => _ProjectDetailPageState();
}

class _ProjectDetailPageState extends State<ProjectDetailPage> {
  List<Scan> _scans = [];
  final Map<int, Report> _reports = {};

  @override
  void initState() { super.initState(); _load(); }

  Future<void> _load() async {
    // 简化：通过创建扫描时的返回缓存扫描记录；此处轮询项目下所有扫描由后端补 endpoints 后实现。
    // 当前方案：App 内本地保存本机创建的扫描 id 列表（SharedPreferences）。
    // 注意：计划 A 任务 11 未提供 GET /api/projects/{id}/scans —— 由实现时补充该端点（返回该机构项目下全部扫描），
    // 或用 GET /api/scans?project_id= 形式；实现任务 4 前先确认后端端点。
  }

  Future<void> _startCapture() async {
    final scan = await context.read<ApiClient>()
        .createScan(widget.project.id, 'video');
    if (!mounted) return;
    Navigator.push(context, MaterialPageRoute(
        builder: (_) => CapturePage(project: widget.project, scan: scan)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.project.name)),
      floatingActionButton: FloatingActionButton(
        onPressed: _startCapture, child: const Icon(Icons.videocam)),
      body: ListView(children: [
        Padding(
          padding: const EdgeInsets.all(16),
          child: Text('扫描记录（${_scans.length}）',
              style: Theme.of(context).textTheme.titleMedium)),
        ..._scans.map((s) => ListTile(
          title: Text('#${s.id}  ${s.status}'),
          subtitle: Text('${s.progress}% ${s.message}'),
          trailing: s.done
              ? const Icon(Icons.description, color: Colors.teal)
              : const Icon(Icons.hourglass_empty),
          onTap: s.done ? () => Navigator.push(context, MaterialPageRoute(
              builder: (_) => ReportPage(scan: s))) : null,
        )),
        const SizedBox(height: 24),
        Center(child: OutlinedButton.icon(
          onPressed: _scans.length >= 2 ? _compare : null,
          icon: const Icon(Icons.compare_arrows),
          label: const Text('改造前后对比'),
        )),
      ]),
    );
  }

  void _compare() {
    // 取最近两次 done 的扫描，跳对比页（report_page 内实现）。
  }
}
```

- [ ] **步骤 3：补充后端端点（配合计划 A 实现时同步完成）**

在计划 A 的 `app/routers/scans.py` 中增加：

```python
@router.get("/projects/{project_id}/scans", response_model=list[ScanOut])
def list_scans(project_id: int, db: Session = Depends(get_db), org_id: int = Depends(get_org_scope)):
    p = db.get(Project, project_id)
    if p is None or p.org_id != org_id:
        raise HTTPException(404, "项目不存在")
    return db.query(Scan).filter(Scan.project_id == project_id).order_by(Scan.id.desc()).all()
```

- [ ] **步骤 4：Commit**

```bash
git add app backend && git commit -m "feat: 项目列表/详情页与扫描记录（含后端列表端点）"
```

---

## 任务 5：采集页（视频录制引导）

**文件：**
- 创建：`lib/pages/capture_page.dart`
- 修改：无

- [ ] **步骤 1：实现采集引导页**

`lib/pages/capture_page.dart`：

```dart
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import '../api/models.dart';
import 'upload_page.dart';

class CapturePage extends StatelessWidget {
  final Project project;
  final Scan scan;
  const CapturePage({super.key, required this.project, required this.scan});

  static const _tips = [
    '1. 找一张 A4 纸，放在地面显眼处（用于尺寸标定）',
    '2. 从门口开始，沿房间边缘慢速走一圈（1~3 分钟）',
    '3. 在角落、门框、卫生间门口停留 2~3 秒',
    '4. 避免逆光拍摄，保证房间光线充足',
    '5. 走完一圈回到门口即可结束',
  ];

  Future<void> _record() async {
    final picker = ImagePicker();
    final video = await picker.pickVideo(source: ImageSource.camera, maxDuration: const Duration(minutes: 5));
    if (video == null) return;
    // 上传页处理上传与进度
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('采集：${project.name}')),
      body: ListView(padding: const EdgeInsets.all(24), children: [
        const Icon(Icons.videocam, size: 64, color: Colors.teal),
        const SizedBox(height: 16),
        const Text('拍摄引导', style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        ..._tips.map((t) => Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Text(t))),
        const SizedBox(height: 24),
        FilledButton.icon(
          onPressed: _record,
          icon: const Icon(Icons.fiber_manual_record),
          label: const Text('开始录制'),
        ),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: _record,
          icon: const Icon(Icons.photo_library),
          label: const Text('改为逐张拍照（备选）'),
        ),
      ]),
    );
  }
}
```

- [ ] **步骤 2：静态检查 + Commit**

```bash
cd app && flutter analyze && git add app && git commit -m "feat: 采集引导页（视频为主，照片备选）"
```

---

## 任务 6：上传页与进度轮询

**文件：**
- 创建：`lib/pages/upload_page.dart`
- 修改：`lib/pages/capture_page.dart`（接通跳转）

- [ ] **步骤 1：实现上传页**

`lib/pages/upload_page.dart`：

```dart
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../api/client.dart';
import '../api/models.dart';
import 'report_page.dart';

class UploadPage extends StatefulWidget {
  final Scan scan;
  final String filePath;
  final String filename;
  const UploadPage({super.key, required this.scan, required this.filePath, required this.filename});
  @override
  State<UploadPage> createState() => _UploadPageState();
}

class _UploadPageState extends State<UploadPage> {
  Timer? _timer;
  Scan _scan = Scan(id: 0, projectId: 0, status: 'uploading', progress: 0, message: '上传中', captureType: 'video');

  @override
  void initState() {
    super.initState();
    _upload();
    _timer = Timer.periodic(const Duration(seconds: 3), (_) => _poll());
  }

  Future<void> _upload() async {
    await context.read<ApiClient>().uploadVideo(widget.scan.id, widget.filePath, widget.filename);
  }

  Future<void> _poll() async {
    try {
      final s = await context.read<ApiClient>().scanStatus(widget.scan.id);
      if (!mounted) return;
      setState(() => _scan = s);
      if (s.done || s.failed) {
        _timer?.cancel();
        if (s.done) {
          Navigator.pushReplacement(context, MaterialPageRoute(
              builder: (_) => ReportPage(scan: s)));
        }
      }
    } catch (_) {/* 网络抖动忽略，下轮再试 */}
  }

  @override
  void dispose() { _timer?.cancel(); super.dispose(); }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('评估进度')),
      body: Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
        CircularProgressIndicator(value: _scan.progress / 100),
        const SizedBox(height: 16),
        Text('${_scan.progress}%'),
        const SizedBox(height: 8),
        Text(_scan.message, style: const TextStyle(color: Colors.grey)),
        const SizedBox(height: 24),
        if (_scan.failed)
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('返回重试')),
      ])),
    );
  }
}
```

- [ ] **步骤 2：接通采集页跳转**

修改 `lib/pages/capture_page.dart` 的 `_record`：

```dart
Future<void> _record() async {
  final picker = ImagePicker();
  final video = await picker.pickVideo(
      source: ImageSource.camera, maxDuration: const Duration(minutes: 5));
  if (video == null) return;
  if (!context.mounted) return;
  Navigator.push(context, MaterialPageRoute(builder: (_) =>
      UploadPage(scan: scan, filePath: video.path,
          filename: video.name.isEmpty ? 'clip.mp4' : video.name)));
}
```

- [ ] **步骤 3：静态检查 + Commit**

```bash
cd app && flutter analyze && git add app && git commit -m "feat: 上传与进度轮询页"
```

---

## 任务 7：报告页（评分/风险/标注图/3D 预览）

**文件：**
- 创建：`lib/pages/report_page.dart`、`lib/widgets/risk_card.dart`、`lib/widgets/score_gauge.dart`
- 修改：无

- [ ] **步骤 1：实现报告页**

`lib/pages/report_page.dart`：

```dart
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../api/client.dart';
import '../api/models.dart';
import '../widgets/risk_card.dart';
import '../widgets/score_gauge.dart';

class ReportPage extends StatefulWidget {
  final Scan scan;
  const ReportPage({super.key, required this.scan});
  @override
  State<ReportPage> createState() => _ReportPageState();
}

class _ReportPageState extends State<ReportPage> {
  Report? _report;
  String? _error;

  @override
  void initState() { super.initState(); _load(); }

  Future<void> _load() async {
    try {
      final r = await context.read<ApiClient>().report(widget.scan.id);
      setState(() { _report = r; _error = null; });
    } catch (e) {
      setState(() => _error = '$e');
    }
  }

  @override
  Widget build(BuildContext context) {
    final r = _report;
    return Scaffold(
      appBar: AppBar(title: const Text('评估报告')),
      body: _error != null
          ? Center(child: Text(_error!))
          : r == null
              ? const Center(child: CircularProgressIndicator())
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(padding: const EdgeInsets.all(16), children: [
                    Center(child: ScoreGauge(score: r.score)),
                    const SizedBox(height: 8),
                    Center(child: Text(r.calibrated == 1
                        ? '已按 A4 纸标定真实尺寸'
                        : r.calibrated == 2
                            ? '已按门高先验标定（精度较低）'
                            : '⚠ 未完成尺寸标定，结果仅供参考')),
                    const SizedBox(height: 16),
                    Text('风险项（${r.risks.length}）',
                        style: Theme.of(context).textTheme.titleMedium),
                    ...r.risks.map((risk) => RiskCard(risk: risk)),
                    const SizedBox(height: 16),
                    Text('改造建议', style: Theme.of(context).textTheme.titleMedium),
                    ...r.advice.map((a) => ListTile(leading: const Icon(Icons.build), title: Text(a))),
                    const SizedBox(height: 16),
                    Text('3D 场景预览', style: Theme.of(context).textTheme.titleMedium),
                    const SizedBox(height: 8),
                    SizedBox(
                      height: 300,
                      child: WebViewPreview(scanId: widget.scan.id),
                    ),
                    const SizedBox(height: 16),
                    Text('标注视图', style: Theme.of(context).textTheme.titleMedium),
                    ...r.images.map((img) => Padding(
                      padding: const EdgeInsets.symmetric(vertical: 4),
                      child: Image.network('${context.read<ApiClient>().dio.options.baseUrl}/static/$img',
                          errorBuilder: (_, __, ___) => const Icon(Icons.broken_image)),
                    )),
                  ]));
  }
}

class WebViewPreview extends StatefulWidget {
  final int scanId;
  const WebViewPreview({super.key, required this.scanId});
  @override
  State<WebViewPreview> createState() => _WebViewPreviewState();
}

class _WebViewPreviewState extends State<WebViewPreview> {
  @override
  Widget build(BuildContext context) {
    // 用 webview_flutter 加载本地打包的渲染器页面，经 JS 通道传入 ply URL。
    // 完整实现依赖计划 A 提供静态文件服务（app/main.py 挂载 /static 指向 data/reports）。
    return Container(
      color: Colors.black,
      alignment: Alignment.center,
      child: const Text('3D 预览加载中…（需 WebView 渲染器）',
          style: TextStyle(color: Colors.white54)),
    );
  }
}
```

- [ ] **步骤 2：实现小部件**

`lib/widgets/score_gauge.dart`：

```dart
import 'package:flutter/material.dart';

class ScoreGauge extends StatelessWidget {
  final double score;
  const ScoreGauge({super.key, required this.score});

  @override
  Widget build(BuildContext context) {
    final color = score >= 80 ? Colors.green : score >= 60 ? Colors.orange : Colors.red;
    return Stack(alignment: Alignment.center, children: [
      SizedBox(
        width: 120, height: 120,
        child: CircularProgressIndicator(
          value: score / 100,
          strokeWidth: 10,
          color: color,
          backgroundColor: Colors.grey.shade200,
        )),
      Column(mainAxisSize: MainAxisSize.min, children: [
        Text('${score.toStringAsFixed(1)}', style: TextStyle(fontSize: 28, fontWeight: FontWeight.bold, color: color)),
        const Text('安全评分', style: TextStyle(fontSize: 12, color: Colors.grey)),
      ]),
    ]);
  }
}
```

`lib/widgets/risk_card.dart`：

```dart
import 'package:flutter/material.dart';
import '../api/models.dart';

class RiskCard extends StatelessWidget {
  final Risk risk;
  const RiskCard({super.key, required this.risk});

  @override
  Widget build(BuildContext context) {
    final color = switch (risk.level) {
      'red' => Colors.red,
      'yellow' => Colors.orange,
      'green' => Colors.green,
      _ => Colors.grey,
    };
    return Card(
      child: ListTile(
        leading: Icon(switch (risk.level) {
          'red' => Icons.dangerous,
          'yellow' => Icons.warning_amber,
          _ => Icons.check_circle,
        }, color: color),
        title: Text(risk.name),
        subtitle: Text(risk.measure?.toString() ?? ''),
        trailing: Text(switch (risk.level) {
          'red' => '高风险', 'yellow' => '注意', _ => '正常',
        }, style: TextStyle(color: color, fontWeight: FontWeight.bold)),
      ));
  }
}
```

- [ ] **步骤 3：静态检查 + Commit**

```bash
cd app && flutter analyze && git add app && git commit -m "feat: 报告页（评分环/风险卡片/标注图/3D 预览容器）"
```

---

## 任务 8：WebGL 3D 预览渲染器

**文件：**
- 创建：`web/preview/index.html`、`web/preview/splat.js`、`web/preview/README.md`
- 修改：`app/main.py`（挂载静态目录，配合计划 A 任务 14）

- [ ] **步骤 1：引入渲染器**

下载 antimatter15/splat 单文件渲染器（Apache-2.0）：

```bash
mkdir -p app/web/preview
curl -L -o app/web/preview/splat.js https://raw.githubusercontent.com/antimatter15/splat/main/splat.js
```

- [ ] **步骤 2：编写预览页**

`app/web/preview/index.html`：

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>安龄智境 3D 预览</title>
<style>html,body{margin:0;height:100%;background:#111;overflow:hidden;color:#ccc;font-family:sans-serif}
#info{position:absolute;top:8px;left:8px;font-size:12px;z-index:2}</style>
</head>
<body>
<div id="info">加载中…（点云预览 · 拖动旋转 · 滚轮缩放）</div>
<canvas id="canvas"></canvas>
<script src="splat.js"></script>
<script>
  // 通过 URL 参数传入 ply 地址：?ply=/static/.../scene.ply
  const params = new URLSearchParams(location.search);
  const plyUrl = params.get('ply');
  const canvas = document.getElementById('canvas');
  if (!plyUrl) {
    document.getElementById('info').textContent = '缺少 ply 参数';
  } else {
    Splat.load(plyUrl, canvas, (progress) => {
      document.getElementById('info').textContent = '加载 ' + Math.round(progress * 100) + '%';
    }).then(() => {
      document.getElementById('info').textContent = '✓ 预览就绪';
    }).catch((e) => {
      document.getElementById('info').textContent = '加载失败: ' + e;
    });
  }
</script>
</body>
</html>
```

- [ ] **步骤 3：后端挂载静态目录**

计划 A `app/main.py` 增加（与任务 14 合并实现）：

```python
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from .config import get_settings

_static = Path(get_settings().data_dir) / "reports"
_static.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static)), name="static")
```

- [ ] **步骤 4：Commit**

```bash
git add app backend && git commit -m "feat: WebGL 3D 预览渲染器与静态资源服务"
```

---

## 任务 9：对比页与打磨

**文件：**
- 创建：`lib/pages/compare_page.dart`
- 修改：`lib/pages/project_detail_page.dart`（接通对比）

- [ ] **步骤 1：实现对比页**

`lib/pages/compare_page.dart`：

```dart
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../api/client.dart';

class ComparePage extends StatefulWidget {
  final int scanA; final int scanB;
  const ComparePage({super.key, required this.scanA, required this.scanB});
  @override
  State<ComparePage> createState() => _ComparePageState();
}

class _ComparePageState extends State<ComparePage> {
  Map<String, dynamic>? _data;

  @override
  void initState() { super.initState(); _load(); }

  Future<void> _load() async {
    final d = await context.read<ApiClient>()
        .compare(widget.scanA, widget.scanB);
    setState(() => _data = d);
  }

  @override
  Widget build(BuildContext context) {
    final d = _data;
    return Scaffold(
      appBar: AppBar(title: const Text('改造前后对比')),
      body: d == null
          ? const Center(child: CircularProgressIndicator())
          : ListView(padding: const EdgeInsets.all(16), children: [
              Center(child: Text(
                '评分变化：${d['before']['score']} → ${d['after']['score']} '
                '（${d['score_delta'] >= 0 ? '+' : ''}${d['score_delta']}）',
                style: TextStyle(
                  fontSize: 22,
                  fontWeight: FontWeight.bold,
                  color: (d['score_delta'] as num) >= 0 ? Colors.green : Colors.red))),
              const SizedBox(height: 16),
              Text('改造前风险', style: Theme.of(context).textTheme.titleMedium),
              ...((d['before']['risks'] as List).map((r) => ListTile(
                  title: Text('${r['name']} · ${r['level']}'),
                  subtitle: Text(r['measure']?.toString() ?? '')))),
              const Divider(),
              Text('改造后风险', style: Theme.of(context).textTheme.titleMedium),
              ...((d['after']['risks'] as List).map((r) => ListTile(
                  title: Text('${r['name']} · ${r['level']}'),
                  subtitle: Text(r['measure']?.toString() ?? '')))),
            ]));
  }
}
```

- [ ] **步骤 2：接通入口（project_detail_page 的 _compare）**

```dart
void _compare() {
  final done = _scans.where((s) => s.done).toList()..sort((a, b) => b.id.compareTo(a.id));
  if (done.length < 2) return;
  Navigator.push(context, MaterialPageRoute(builder: (_) =>
      ComparePage(scanA: done.last.id, scanB: done.first.id)));
}
```

- [ ] **步骤 3：全量检查**

```bash
cd app && flutter analyze && flutter test
```

预期：No issues；全部测试 PASS。

- [ ] **步骤 4：Commit**

```bash
git add app && git commit -m "feat: 改造前后对比页与收尾打磨"
```

---

## 自检记录

**规格覆盖度：**
- 视频采集（随意录引导）→ 任务 5
- 照片备选 → 任务 5（按钮入口，实现时复用 picker 多选）
- 上传 + 进度 → 任务 6
- 报告（评分/风险/建议/标注图）→ 任务 7
- 交互式 3D 预览 → 任务 8
- 多机构登录 → 任务 3
- 历史记录/对比 → 任务 4/9
- 无 Web 后台 → 与规格一致（不实现）

**占位符扫描：** 无 TODO；`project_detail_page` 的扫描列表依赖后端 `GET /api/projects/{id}/scans` 端点，已在任务 4 步骤 3 给出后端补充代码，并与计划 A 交叉引用（实现计划 A 时同步加入）。

**类型一致性：** `ApiClient` 方法名与后端端点路径一致（register/login/projects/createProject/createScan/uploadVideo/scanStatus/report/compare）；`Report`/`Scan` 字段名与后端 JSON 键一致（scan_id/status/progress/message/capture_type/risks/advice/images/calibrated/score）。

**执行交接：** 计划完成后，两种执行方式（subagent-driven-development 或 executing-plans 内联执行）任选。

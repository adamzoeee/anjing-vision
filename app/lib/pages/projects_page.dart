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
      if (!mounted) return;
      setState(() {
        _projects = list;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = '$e');
    }
  }

  Future<void> _create() async {
    final client = context.read<ApiClient>();
    final name = await showDialog<String>(
      context: context,
      builder: (c) {
        final tc = TextEditingController();
        return AlertDialog(
          title: const Text('新建评估项目'),
          content: TextField(
            controller: tc,
            decoration: const InputDecoration(labelText: '项目名（如：王奶奶家）'),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(c),
              child: const Text('取消'),
            ),
            TextButton(
              onPressed: () => Navigator.pop(c, tc.text),
              child: const Text('创建'),
            ),
          ],
        );
      },
    );
    if (name != null && name.isNotEmpty) {
      await client.createProject(name, '');
      _load();
    }
  }

  @override
  Widget build(BuildContext context) {
    final store = context.watch<AuthStore>();
    return Scaffold(
      appBar: AppBar(
        title: Text('项目列表（${store.user?.orgName ?? ''}）'),
        actions: [
          IconButton(
            icon: const Icon(Icons.logout),
            onPressed: () async {
              await store.logout();
              if (!context.mounted) return;
              Navigator.of(
                context,
              ).pushNamedAndRemoveUntil('/login', (route) => false);
            },
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: _create,
        child: const Icon(Icons.add),
      ),
      body: _error != null
          ? Center(child: Text(_error!))
          : _projects == null
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: _projects!.isEmpty
                  ? ListView(
                      children: const [
                        SizedBox(height: 120),
                        Center(child: Text('暂无项目，点击右下角 + 新建')),
                      ],
                    )
                  : ListView.builder(
                      itemCount: _projects!.length,
                      itemBuilder: (_, i) {
                        final p = _projects![i];
                        return ListTile(
                          title: Text(p.name),
                          subtitle: Text(
                            p.address.isEmpty ? '点击进入' : p.address,
                          ),
                          trailing: const Icon(Icons.chevron_right),
                          onTap: () => Navigator.push(
                            context,
                            MaterialPageRoute(
                              builder: (_) => ProjectDetailPage(project: p),
                            ),
                          ),
                        );
                      },
                    ),
            ),
    );
  }
}

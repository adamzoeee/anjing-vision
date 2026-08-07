import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../state/auth_store.dart';

class LoginPage extends StatefulWidget {
  final bool navigateOnSuccess;

  const LoginPage({super.key, this.navigateOnSuccess = true});

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final _email = TextEditingController();
  final _pw = TextEditingController();
  bool _register = false;
  final _org = TextEditingController();
  final _name = TextEditingController();

  @override
  void dispose() {
    _email.dispose();
    _pw.dispose();
    _org.dispose();
    _name.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final store = context.read<AuthStore>();
    final ok = _register
        ? await store.register(_org.text, _name.text, _email.text, _pw.text)
        : await store.login(_email.text, _pw.text);
    if (ok && mounted && widget.navigateOnSuccess) {
      Navigator.of(context).pushReplacementNamed('/projects');
    }
  }

  @override
  Widget build(BuildContext context) {
    final store = context.watch<AuthStore>();
    return Scaffold(
      appBar: AppBar(title: const Text('安龄智境')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          children: [
            const SizedBox(height: 16),
            const Icon(Icons.home_work, size: 64, color: Colors.teal),
            const SizedBox(height: 8),
            const Text(
              '老人居住空间安全评估',
              style: TextStyle(fontSize: 16, color: Colors.grey),
            ),
            const SizedBox(height: 32),
            TextField(
              controller: _email,
              decoration: const InputDecoration(labelText: '邮箱'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _pw,
              obscureText: true,
              decoration: const InputDecoration(labelText: '密码'),
            ),
            if (_register) ...[
              const SizedBox(height: 12),
              TextField(
                controller: _org,
                decoration: const InputDecoration(labelText: '机构名称'),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _name,
                decoration: const InputDecoration(labelText: '姓名'),
              ),
            ],
            if (store.error != null) ...[
              const SizedBox(height: 12),
              Text(store.error!, style: const TextStyle(color: Colors.red)),
            ],
            const SizedBox(height: 24),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: store.loading ? null : _submit,
                child: Text(_register ? '注册并登录' : '登录'),
              ),
            ),
            TextButton(
              onPressed: () => setState(() => _register = !_register),
              child: Text(_register ? '已有账号？去登录' : '没有账号？注册'),
            ),
          ],
        ),
      ),
    );
  }
}

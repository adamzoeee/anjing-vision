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
    return MultiProvider(
      providers: [
        Provider<ApiClient>.value(value: api),
        ChangeNotifierProvider(create: (_) => AuthStore(api)..restore()),
      ],
      child: MaterialApp(
        title: '安龄智境',
        theme: ThemeData(colorSchemeSeed: Colors.teal, useMaterial3: true),
        home: const SessionGate(),
        routes: {
          '/login': (_) => const LoginPage(),
          '/projects': (_) => const ProjectsPage(),
        },
      ),
    );
  }
}

class SessionGate extends StatelessWidget {
  const SessionGate({super.key});

  @override
  Widget build(BuildContext context) {
    final store = context.watch<AuthStore>();
    if (store.restoring) {
      return const Scaffold(
        body: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              CircularProgressIndicator(),
              SizedBox(height: 16),
              Text('正在恢复登录状态…'),
            ],
          ),
        ),
      );
    }
    if (store.restoreFailed) {
      return Scaffold(
        body: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(store.error ?? '恢复登录状态失败'),
              const SizedBox(height: 16),
              ElevatedButton(onPressed: store.restore, child: const Text('重试')),
            ],
          ),
        ),
      );
    }
    if (store.authenticated) return const ProjectsPage();
    return const LoginPage(navigateOnSuccess: false);
  }
}

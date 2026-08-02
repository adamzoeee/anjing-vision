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

  void logout() {
    user = null;
    SharedPreferences.getInstance().then((p) => p.remove('token'));
    api.setToken(null);
    notifyListeners();
  }
}

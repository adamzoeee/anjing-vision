import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../api/client.dart';
import '../api/models.dart';

enum AuthStatus { restoring, authenticated, unauthenticated }

abstract interface class TokenStorage {
  Future<String?> read();
  Future<void> write(String token);
  Future<void> clear();
}

class SharedPreferencesTokenStorage implements TokenStorage {
  @override
  Future<String?> read() async =>
      (await SharedPreferences.getInstance()).getString('token');

  @override
  Future<void> write(String token) async {
    await (await SharedPreferences.getInstance()).setString('token', token);
  }

  @override
  Future<void> clear() async {
    await (await SharedPreferences.getInstance()).remove('token');
  }
}

class AuthStore extends ChangeNotifier {
  final ApiClient api;
  final TokenStorage _tokenStorage;

  AuthStore(this.api, {TokenStorage? tokenStorage})
    : _tokenStorage = tokenStorage ?? SharedPreferencesTokenStorage();

  AuthUser? user;
  bool loading = false;
  String? error;
  AuthStatus status = AuthStatus.restoring;

  bool get restoring => status == AuthStatus.restoring;
  bool get authenticated => status == AuthStatus.authenticated && user != null;

  Future<bool> login(String email, String password) async {
    loading = true;
    error = null;
    notifyListeners();
    try {
      final r = await api.login(email: email, password: password);
      user = r.user;
      await _persist(r.token);
      status = AuthStatus.authenticated;
      return true;
    } catch (e) {
      status = AuthStatus.unauthenticated;
      error = '登录失败: $e';
      return false;
    } finally {
      loading = false;
      notifyListeners();
    }
  }

  Future<bool> register(
    String org,
    String name,
    String email,
    String password,
  ) async {
    loading = true;
    error = null;
    notifyListeners();
    try {
      final r = await api.register(
        orgName: org,
        name: name,
        email: email,
        password: password,
      );
      user = r.user;
      await _persist(r.token);
      status = AuthStatus.authenticated;
      return true;
    } catch (e) {
      status = AuthStatus.unauthenticated;
      error = '注册失败: $e';
      return false;
    } finally {
      loading = false;
      notifyListeners();
    }
  }

  Future<void> _persist(String token) async {
    await _tokenStorage.write(token);
    api.setToken(token);
  }

  Future<void> restore() async {
    status = AuthStatus.restoring;
    error = null;
    notifyListeners();

    final token = await _tokenStorage.read();
    if (token == null || token.isEmpty) {
      user = null;
      api.setToken(null);
      status = AuthStatus.unauthenticated;
      notifyListeners();
      return;
    }

    api.setToken(token);
    try {
      user = await api.me();
      status = AuthStatus.authenticated;
    } catch (_) {
      await _clearSession();
      status = AuthStatus.unauthenticated;
    }
    notifyListeners();
  }

  Future<void> logout() async {
    error = null;
    await _clearSession();
    status = AuthStatus.unauthenticated;
    notifyListeners();
  }

  Future<void> _clearSession() async {
    user = null;
    api.setToken(null);
    await _tokenStorage.clear();
  }
}

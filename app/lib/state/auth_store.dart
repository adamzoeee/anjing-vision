import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../api/client.dart';
import '../api/models.dart';

enum AuthStatus { restoring, authenticated, unauthenticated, restoreFailed }

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
  bool get restoreFailed => status == AuthStatus.restoreFailed;

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

    String? token;
    try {
      token = await _tokenStorage.read();
    } catch (_) {
      _markRestoreFailed();
      notifyListeners();
      return;
    }

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
    } on DioException catch (exception) {
      final statusCode = exception.response?.statusCode;
      if (statusCode == 401 || statusCode == 403) {
        await _clearSession();
        status = AuthStatus.unauthenticated;
      } else {
        _markRestoreFailed();
      }
    } catch (_) {
      _markRestoreFailed();
    }
    notifyListeners();
  }

  void _markRestoreFailed() {
    user = null;
    status = AuthStatus.restoreFailed;
    error = '恢复登录状态失败，请检查网络或稍后重试';
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

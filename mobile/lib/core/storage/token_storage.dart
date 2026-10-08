import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class AuthTokens {
  const AuthTokens({required this.accessToken, required this.refreshToken});

  final String accessToken;
  final String refreshToken;
}

abstract interface class TokenStorage {
  Future<AuthTokens?> read();
  Future<void> write(AuthTokens tokens);
  Future<void> clear();
}

class SecureTokenStorage implements TokenStorage {
  SecureTokenStorage([FlutterSecureStorage? storage])
    : _storage = storage ?? const FlutterSecureStorage();

  static const _accessKey = 'm1917_access_token';
  static const _refreshKey = 'm1917_refresh_token';
  final FlutterSecureStorage _storage;

  @override
  Future<AuthTokens?> read() async {
    final values = await Future.wait([
      _storage.read(key: _accessKey),
      _storage.read(key: _refreshKey),
    ]).timeout(const Duration(seconds: 5));
    if (values[0] == null || values[1] == null) return null;
    return AuthTokens(accessToken: values[0]!, refreshToken: values[1]!);
  }

  @override
  Future<void> write(AuthTokens tokens) async {
    // Web storage creates its encryption key lazily. Parallel first writes can
    // generate two keys and leave one token encrypted with a discarded key.
    await _storage
        .write(key: _accessKey, value: tokens.accessToken)
        .timeout(const Duration(seconds: 5));
    await _storage
        .write(key: _refreshKey, value: tokens.refreshToken)
        .timeout(const Duration(seconds: 5));
  }

  @override
  Future<void> clear() async {
    await Future.wait([
      _storage.delete(key: _accessKey),
      _storage.delete(key: _refreshKey),
    ]).timeout(const Duration(seconds: 5));
  }
}

class MemoryTokenStorage implements TokenStorage {
  AuthTokens? value;

  @override
  Future<void> clear() async => value = null;

  @override
  Future<AuthTokens?> read() async => value;

  @override
  Future<void> write(AuthTokens tokens) async => value = tokens;
}

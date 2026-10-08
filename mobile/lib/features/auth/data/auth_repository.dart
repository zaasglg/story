import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/config/app_config.dart';
import '../../../core/network/api_client.dart';
import '../../../core/storage/token_storage.dart';
import '../domain/auth_user.dart';

abstract interface class AuthRepository {
  Future<AuthUser?> restoreSession();
  Future<void> requestOtp(String phone);
  Future<AuthUser> verifyOtp({
    required String phone,
    required String code,
    required String deviceName,
  });
  Future<void> logout();
}

class ApiAuthRepository implements AuthRepository {
  ApiAuthRepository(this._client, this._storage);

  final ApiClient _client;
  final TokenStorage _storage;

  @override
  Future<AuthUser?> restoreSession() async {
    try {
      if (await _storage.read() == null) return null;
      final response = await _client.dio.get<Map<String, dynamic>>('/me');
      return AuthUser.fromJson(response.data!);
    } catch (_) {
      try {
        await _storage.clear();
      } catch (_) {
        // Storage may itself be unavailable; still allow a fresh login.
      }
      return null;
    }
  }

  @override
  Future<void> requestOtp(String phone) async {
    await _client.dio.post<void>('/auth/request-otp', data: {'phone': phone});
  }

  @override
  Future<AuthUser> verifyOtp({
    required String phone,
    required String code,
    required String deviceName,
  }) async {
    final response = await _client.dio.post<Map<String, dynamic>>(
      '/auth/verify-otp',
      data: {'phone': phone, 'code': code, 'device_name': deviceName},
    );
    final data = response.data!;
    await _storage.write(
      AuthTokens(
        accessToken: data['access_token'] as String,
        refreshToken: data['refresh_token'] as String,
      ),
    );
    return AuthUser.fromJson(data['user'] as Map<String, dynamic>);
  }

  @override
  Future<void> logout() async {
    final tokens = await _storage.read();
    try {
      await _client.dio.post<void>(
        '/auth/logout',
        data: {'refresh_token': tokens?.refreshToken},
      );
    } finally {
      await _storage.clear();
    }
  }
}

final tokenStorageProvider = Provider<TokenStorage>(
  (ref) => SecureTokenStorage(),
);

final apiClientProvider = Provider<ApiClient>((ref) {
  return ApiClient(
    baseUrl: AppConfig.apiBaseUrl,
    tokenStorage: ref.watch(tokenStorageProvider),
  );
});

final authRepositoryProvider = Provider<AuthRepository>((ref) {
  return ApiAuthRepository(
    ref.watch(apiClientProvider),
    ref.watch(tokenStorageProvider),
  );
});

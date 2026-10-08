import 'dart:async';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:m1917/core/network/api_client.dart';
import 'package:m1917/core/storage/token_storage.dart';
import 'package:m1917/features/auth/data/auth_repository.dart';

class DelayedSecureStorage extends FlutterSecureStorage {
  final firstWrite = Completer<void>();
  final keys = <String>[];

  @override
  Future<void> write({
    required String key,
    required String? value,
    AppleOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    AppleOptions? mOptions,
    WindowsOptions? wOptions,
  }) async {
    keys.add(key);
    if (keys.length == 1) await firstWrite.future;
  }
}

class BrokenTokenStorage implements TokenStorage {
  @override
  Future<AuthTokens?> read() async => throw StateError('Storage unavailable');
  @override
  Future<void> clear() async => throw StateError('Storage unavailable');
  @override
  Future<void> write(AuthTokens tokens) async =>
      throw StateError('Storage unavailable');
}

class PublicAuthAdapter implements HttpClientAdapter {
  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async => ResponseBody.fromString('{}', 202);
  @override
  void close({bool force = false}) {}
}

void main() {
  test(
    'secure token writes wait for first encryption key initialization',
    () async {
      final underlying = DelayedSecureStorage();
      final storage = SecureTokenStorage(underlying);
      final writing = storage.write(
        const AuthTokens(accessToken: 'access', refreshToken: 'refresh'),
      );
      await Future<void>.delayed(Duration.zero);
      expect(underlying.keys, ['m1917_access_token']);
      underlying.firstWrite.complete();
      await writing;
      expect(underlying.keys, ['m1917_access_token', 'm1917_refresh_token']);
    },
  );

  test('storage failures reject API requests instead of hanging', () async {
    final client = ApiClient(
      baseUrl: 'https://test.invalid',
      tokenStorage: BrokenTokenStorage(),
    );
    await expectLater(
      client.dio.get<void>('/me').timeout(const Duration(seconds: 2)),
      throwsA(isA<DioException>()),
    );
    client.dio.close();
  });

  test(
    'public login does not depend on reading broken token storage',
    () async {
      final storage = BrokenTokenStorage();
      final client = ApiClient(
        baseUrl: 'https://test.invalid',
        tokenStorage: storage,
      );
      client.dio.httpClientAdapter = PublicAuthAdapter();
      final repository = ApiAuthRepository(client, storage);
      expect(await repository.restoreSession(), isNull);
      await repository.requestOtp('+77000000004');
      client.dio.close();
    },
  );
}

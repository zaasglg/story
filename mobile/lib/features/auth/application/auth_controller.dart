import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/auth_repository.dart';
import '../domain/auth_user.dart';

class AuthController extends AsyncNotifier<AuthUser?> {
  AuthRepository get _repository => ref.read(authRepositoryProvider);

  @override
  Future<AuthUser?> build() => _repository.restoreSession();

  Future<void> requestOtp(String phone) => _repository.requestOtp(phone);

  Future<void> verifyOtp({required String phone, required String code}) async {
    state = const AsyncLoading();
    final result = await AsyncValue.guard(
      () => _repository.verifyOtp(
        phone: phone,
        code: code,
        deviceName: 'ALASH Mobile',
      ),
    );
    state = result;
    if (result.hasError) {
      Error.throwWithStackTrace(result.error!, result.stackTrace!);
    }
  }

  Future<void> logout() async {
    state = const AsyncLoading();
    try {
      await _repository.logout();
    } finally {
      state = const AsyncData(null);
    }
  }
}

final authControllerProvider = AsyncNotifierProvider<AuthController, AuthUser?>(
  AuthController.new,
);

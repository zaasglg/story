import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:m1917/app/app.dart';
import 'package:m1917/app/router.dart';
import 'package:m1917/features/auth/data/auth_repository.dart';
import 'package:m1917/features/auth/domain/auth_user.dart';
import 'package:m1917/features/workforce/data/workforce_repository.dart';
import 'package:m1917/features/workforce/domain/worker_summary.dart';

class FakeAuthRepository implements AuthRepository {
  FakeAuthRepository([this.user]);

  AuthUser? user;

  @override
  Future<void> logout() async => user = null;

  @override
  Future<void> requestOtp(String phone) async {}

  @override
  Future<AuthUser?> restoreSession() async => user;

  @override
  Future<AuthUser> verifyOtp({
    required String phone,
    required String code,
    required String deviceName,
  }) async {
    return user!;
  }
}

void main() {
  test('roles have fixed destinations', () {
    expect(destinationForRole(UserRole.worker), '/worker');
    expect(destinationForRole(UserRole.master), '/master');
    expect(destinationForRole(UserRole.admin), '/web-panel');
    expect(destinationForRole(UserRole.boss), '/web-panel');
  });

  testWidgets('login never asks the user to select a role', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          authRepositoryProvider.overrideWithValue(FakeAuthRepository()),
        ],
        child: const M1917App(),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Телефон нөмірі'), findsOneWidget);
    expect(find.text('Код алу'), findsOneWidget);
    expect(find.text('Рөл'), findsNothing);
    expect(find.byType(DropdownButton<UserRole>), findsNothing);
  });

  testWidgets('worker session routes only to worker shell', (tester) async {
    const worker = AuthUser(
      id: 'worker-id',
      phone: '+77071234567',
      role: UserRole.worker,
      status: 'ACTIVE',
      fullName: 'Оразғали Асан',
    );
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          authRepositoryProvider.overrideWithValue(FakeAuthRepository(worker)),
          ownProfileProvider.overrideWith(
            (ref) async => const WorkerSummary(
              id: 'worker-id',
              fullName: 'Оразғали Асан',
              city: 'Lipetsk',
              brigadeName: null,
            ),
          ),
          publicWorkersProvider.overrideWith((ref) async => const []),
        ],
        child: const M1917App(),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Оразғали Асан'), findsOneWidget);
    expect(find.text('Менің профилім'), findsOneWidget);
    expect(find.text('Менің бригадам'), findsNothing);
  });
}

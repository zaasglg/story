import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../features/auth/application/auth_controller.dart';
import '../features/auth/domain/auth_user.dart';
import '../features/auth/presentation/login_screen.dart';
import '../features/home/role_home_screen.dart';
import '../features/home/splash_screen.dart';

String destinationForRole(UserRole role) {
  return switch (role) {
    UserRole.worker => '/worker',
    UserRole.master => '/master',
    UserRole.admin || UserRole.boss => '/web-panel',
  };
}

final routerProvider = Provider<GoRouter>((ref) {
  final auth = ref.watch(authControllerProvider);
  return GoRouter(
    initialLocation: '/splash',
    redirect: (context, state) {
      if (auth.isLoading) {
        return state.matchedLocation == '/splash' ? null : '/splash';
      }
      final user = auth.value;
      if (user == null) {
        return state.matchedLocation == '/login' ? null : '/login';
      }
      final target = destinationForRole(user.role);
      return state.matchedLocation == target ? null : target;
    },
    routes: [
      GoRoute(path: '/splash', builder: (_, _) => const SplashScreen()),
      GoRoute(path: '/login', builder: (_, _) => const LoginScreen()),
      GoRoute(
        path: '/worker',
        builder: (_, _) => const RoleHomeScreen(expectedRole: UserRole.worker),
      ),
      GoRoute(
        path: '/master',
        builder: (_, _) => const RoleHomeScreen(expectedRole: UserRole.master),
      ),
      GoRoute(
        path: '/web-panel',
        builder: (_, _) => const WebPanelNoticeScreen(),
      ),
    ],
  );
});

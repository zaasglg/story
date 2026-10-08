import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/network/api_client.dart';
import '../../auth/data/auth_repository.dart';
import '../domain/worker_summary.dart';

class WorkforceRepository {
  WorkforceRepository(this._client);

  final ApiClient _client;

  Future<WorkerSummary> ownProfile() async {
    final response = await _client.dio.get<Map<String, dynamic>>('/workers/me');
    return WorkerSummary.fromJson(response.data!);
  }

  Future<List<WorkerSummary>> publicWorkers() async {
    final response = await _client.dio.get<List<dynamic>>('/workers/public');
    return response.data!
        .map((item) => WorkerSummary.fromJson(item as Map<String, dynamic>))
        .toList(growable: false);
  }

  Future<List<WorkerSummary>> masterTeam() async {
    final response = await _client.dio.get<List<dynamic>>('/master/team');
    return response.data!
        .map((item) => WorkerSummary.fromJson(item as Map<String, dynamic>))
        .toList(growable: false);
  }

  Future<List<WorkObjectSummary>> masterObjects() async {
    final response = await _client.dio.get<List<dynamic>>('/master/objects');
    return response.data!
        .map((item) => WorkObjectSummary.fromJson(item as Map<String, dynamic>))
        .toList(growable: false);
  }
}

final workforceRepositoryProvider = Provider<WorkforceRepository>(
  (ref) => WorkforceRepository(ref.watch(apiClientProvider)),
);

final ownProfileProvider = FutureProvider<WorkerSummary>(
  (ref) => ref.watch(workforceRepositoryProvider).ownProfile(),
);

final publicWorkersProvider = FutureProvider<List<WorkerSummary>>(
  (ref) => ref.watch(workforceRepositoryProvider).publicWorkers(),
);

final masterTeamProvider = FutureProvider<List<WorkerSummary>>(
  (ref) => ref.watch(workforceRepositoryProvider).masterTeam(),
);

final masterObjectsProvider = FutureProvider<List<WorkObjectSummary>>(
  (ref) => ref.watch(workforceRepositoryProvider).masterObjects(),
);

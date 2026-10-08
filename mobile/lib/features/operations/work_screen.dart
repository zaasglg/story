import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../auth/data/auth_repository.dart';

final workDaysProvider = FutureProvider.autoDispose
    .family<List<Map<String, dynamic>>, String>((ref, path) async {
      final response = await ref
          .watch(apiClientProvider)
          .dio
          .get<List<dynamic>>(path);
      return response.data!.cast<Map<String, dynamic>>();
    });

class WorkScreen extends ConsumerWidget {
  const WorkScreen({required this.path, super.key});

  final String path;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final provider = workDaysProvider(path);
    return ref
        .watch(provider)
        .when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (_, _) => Center(
            child: FilledButton(
              onPressed: () => ref.invalidate(provider),
              child: const Text('Қайта жүктеу'),
            ),
          ),
          data: (days) => RefreshIndicator(
            onRefresh: () async {
              ref.invalidate(provider);
              await ref.read(provider.future);
            },
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.all(16),
              children: [
                if (days.isEmpty)
                  const ListTile(title: Text('Жұмыс күндері әлі жоқ')),
                for (final day in days)
                  ListTile(
                    title: Text('${day['work_date']} · ${day['object_name']}'),
                    subtitle: Text(
                      '${day['hours']} сағат · ${day['m3']} м³ · ${day['status']}',
                    ),
                    trailing: day.containsKey('daily_earnings')
                        ? Text('${day['daily_earnings']} ${day['currency']}')
                        : null,
                  ),
              ],
            ),
          ),
        );
  }
}

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'work_screen.dart';

class OwnEquipmentScreen extends ConsumerWidget {
  const OwnEquipmentScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final provider = workDaysProvider('/workers/me/equipment');
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
          data: (issues) => RefreshIndicator(
            onRefresh: () async {
              ref.invalidate(provider);
              await ref.read(provider.future);
            },
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              children: [
                if (issues.isEmpty)
                  const ListTile(title: Text('Берілген жабдық жоқ')),
                for (final issue in issues)
                  ListTile(
                    leading: const Icon(Icons.construction),
                    title: Text(
                      '${issue['equipment_name']} × ${issue['quantity']}',
                    ),
                    subtitle: Text(
                      '${issue['issue_date']}${issue['return_date'] == null ? '' : ' — ${issue['return_date']}'}',
                    ),
                    trailing: Text(
                      issue['return_date'] == null ? 'Берілді' : 'Қайтарылды',
                    ),
                  ),
              ],
            ),
          ),
        );
  }
}

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'work_screen.dart';

class OwnFinanceScreen extends ConsumerStatefulWidget {
  const OwnFinanceScreen({super.key});

  @override
  ConsumerState<OwnFinanceScreen> createState() => _OwnFinanceScreenState();
}

class _OwnFinanceScreenState extends ConsumerState<OwnFinanceScreen> {
  DateTime month = DateTime(DateTime.now().year, DateTime.now().month);

  @override
  Widget build(BuildContext context) {
    final period = '${month.year}-${month.month.toString().padLeft(2, '0')}-01';
    final provider = workDaysProvider('/workers/me/finance?period=$period');
    return Column(
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            IconButton(
              tooltip: 'Алдыңғы ай',
              onPressed: () =>
                  setState(() => month = DateTime(month.year, month.month - 1)),
              icon: const Icon(Icons.chevron_left),
            ),
            Text('${month.month.toString().padLeft(2, '0')}.${month.year}'),
            IconButton(
              tooltip: 'Келесі ай',
              onPressed: () =>
                  setState(() => month = DateTime(month.year, month.month + 1)),
              icon: const Icon(Icons.chevron_right),
            ),
          ],
        ),
        Expanded(
          child: ref
              .watch(provider)
              .when(
                loading: () => const Center(child: CircularProgressIndicator()),
                error: (_, _) => Center(
                  child: FilledButton(
                    onPressed: () => ref.invalidate(provider),
                    child: const Text('Қайта жүктеу'),
                  ),
                ),
                data: (rows) => RefreshIndicator(
                  onRefresh: () async {
                    ref.invalidate(provider);
                    await ref.read(provider.future);
                  },
                  child: ListView(
                    physics: const AlwaysScrollableScrollPhysics(),
                    padding: const EdgeInsets.all(16),
                    children: [
                      if (rows.isEmpty)
                        const ListTile(title: Text('Бекітілген төлемдер жоқ')),
                      for (final row in rows) ...[
                        Text(
                          '${row['currency']}',
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        for (final field in {
                          'salary': 'Жалақы',
                          'bonus': 'Бонус',
                          'penalty': 'Айыппұл',
                          'advance': 'Аванс',
                          'payment': 'Төленді',
                          'monthly_pay': 'Айлық есеп',
                          'remaining': 'Қалдық',
                        }.entries)
                          ListTile(
                            title: Text(field.value),
                            trailing: Text('${row[field.key]}'),
                          ),
                      ],
                    ],
                  ),
                ),
              ),
        ),
      ],
    );
  }
}

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'work_screen.dart';

class ScheduleCalendar extends ConsumerStatefulWidget {
  const ScheduleCalendar({required this.path, super.key});
  final String path;

  @override
  ConsumerState<ScheduleCalendar> createState() => _ScheduleCalendarState();
}

class _ScheduleCalendarState extends ConsumerState<ScheduleCalendar> {
  DateTime selected = DateTime.now();

  @override
  Widget build(BuildContext context) {
    final provider = workDaysProvider(widget.path);
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
          data: (schedules) {
            final key =
                '${selected.year.toString().padLeft(4, '0')}-${selected.month.toString().padLeft(2, '0')}-${selected.day.toString().padLeft(2, '0')}';
            final shifts = schedules
                .where((item) => item['work_date'] == key)
                .toList();
            return RefreshIndicator(
              onRefresh: () async {
                ref.invalidate(provider);
                await ref.read(provider.future);
              },
              child: ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.all(16),
                children: [
                  CalendarDatePicker(
                    initialDate: selected,
                    firstDate: DateTime(2000),
                    lastDate: DateTime(2100),
                    onDateChanged: (date) => setState(() => selected = date),
                  ),
                  Text(key, style: Theme.of(context).textTheme.titleLarge),
                  if (shifts.isEmpty)
                    const ListTile(title: Text('Бұл күні ауысым жоқ')),
                  for (final shift in shifts)
                    ListTile(
                      title: Text(
                        '${shift['worker_name']} · ${shift['object_name']}',
                      ),
                      subtitle: Text(
                        '${shift['shift_start']} — ${shift['shift_end']} · ${shift['brigade_name']}',
                      ),
                    ),
                ],
              ),
            );
          },
        );
  }
}

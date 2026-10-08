import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:m1917/features/operations/calendar_screen.dart';
import 'package:m1917/features/operations/work_screen.dart';

void main() {
  testWidgets('calendar shows only shifts for the selected day', (
    tester,
  ) async {
    final today = DateTime.now();
    final date =
        '${today.year}-${today.month.toString().padLeft(2, '0')}-${today.day.toString().padLeft(2, '0')}';
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          workDaysProvider('/workers/me/schedules').overrideWith(
            (ref) async => [
              {
                'work_date': date,
                'worker_name': 'Worker',
                'object_name': 'Today object',
                'brigade_name': 'Team',
                'shift_start': '08:00',
                'shift_end': '17:00',
              },
              {
                'work_date': '2001-01-01',
                'worker_name': 'Worker',
                'object_name': 'Old object',
                'brigade_name': 'Team',
                'shift_start': '08:00',
                'shift_end': '17:00',
              },
            ],
          ),
        ],
        child: const MaterialApp(
          home: Scaffold(body: ScheduleCalendar(path: '/workers/me/schedules')),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Worker · Today object'), findsOneWidget);
    expect(find.text('Worker · Old object'), findsNothing);
  });
}

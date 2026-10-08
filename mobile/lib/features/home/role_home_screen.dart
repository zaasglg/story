import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../auth/application/auth_controller.dart';
import '../auth/domain/auth_user.dart';
import '../workforce/data/workforce_repository.dart';
import '../workforce/domain/worker_summary.dart';
import '../operations/work_screen.dart';
import '../operations/calendar_screen.dart';
import '../operations/equipment_screen.dart';
import '../operations/finance_screen.dart';
import '../operations/communications_screen.dart';
import '../operations/pass_rating_screen.dart';
import '../operations/support_screen.dart';
import '../operations/emergency_screen.dart';

class RoleHomeScreen extends ConsumerWidget {
  const RoleHomeScreen({required this.expectedRole, super.key});

  final UserRole expectedRole;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return DefaultTabController(
      length: expectedRole == UserRole.worker ? 11 : 6,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('ALASH | M-1917'),
          actions: [
            IconButton(
              tooltip: 'Шұғыл байланыс: 112',
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => const EmergencyScreen(),
                ),
              ),
              icon: const Icon(Icons.emergency_outlined),
            ),
            IconButton(
              tooltip: 'Шығу',
              onPressed: () =>
                  ref.read(authControllerProvider.notifier).logout(),
              icon: const Icon(Icons.logout),
            ),
          ],
          bottom: TabBar(
            isScrollable: true,
            tabs: expectedRole == UserRole.worker
                ? const [
                    Tab(text: 'Менің профилім'),
                    Tab(text: 'Қызметкерлер'),
                    Tab(text: 'Жұмыс'),
                    Tab(text: 'Күнтізбе'),
                    Tab(text: 'Жабдық'),
                    Tab(text: 'Қаржы'),
                    Tab(text: 'Рұқсат'),
                    Tab(text: 'Рейтинг'),
                    Tab(text: 'Хабарландыру'),
                    Tab(text: 'Чат'),
                    Tab(text: 'Қолдау'),
                  ]
                : const [
                    Tab(text: 'Менің бригадам'),
                    Tab(text: 'Объектілер'),
                    Tab(text: 'Күнтізбе'),
                    Tab(text: 'Хабарландыру'),
                    Tab(text: 'Чат'),
                    Tab(text: 'Қолдау'),
                  ],
          ),
        ),
        body: TabBarView(
          children: expectedRole == UserRole.worker
              ? const [
                  _OwnProfileTab(),
                  _PublicWorkersTab(),
                  WorkScreen(path: '/workers/me/work-days'),
                  ScheduleCalendar(path: '/workers/me/schedules'),
                  OwnEquipmentScreen(),
                  OwnFinanceScreen(),
                  PassRatingScreen(isPass: true),
                  PassRatingScreen(isPass: false),
                  CommunicationsScreen(notifications: true),
                  CommunicationsScreen(
                    notifications: false,
                    canOpenManager: true,
                  ),
                  SupportScreen(),
                ]
              : const [
                  _MasterTeamTab(),
                  _MasterObjectsTab(),
                  ScheduleCalendar(path: '/master/schedules'),
                  CommunicationsScreen(notifications: true),
                  CommunicationsScreen(notifications: false),
                  SupportScreen(),
                ],
        ),
      ),
    );
  }
}

class _OwnProfileTab extends ConsumerWidget {
  const _OwnProfileTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return _AsyncBody<WorkerSummary>(
      value: ref.watch(ownProfileProvider),
      onRefresh: () => ref.refresh(ownProfileProvider.future),
      builder: (item) => ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Text(item.fullName, style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 16),
          _InfoRow(label: 'Телефон', value: item.phone),
          _InfoRow(label: 'Табель нөмірі', value: item.employeeId),
          _InfoRow(label: 'Қала', value: item.city),
          _InfoRow(label: 'Бригада', value: item.brigadeName),
          _InfoRow(label: 'Объект', value: item.objectName),
          _InfoRow(label: 'Паспорт', value: item.passportNumber),
        ],
      ),
    );
  }
}

class _PublicWorkersTab extends ConsumerWidget {
  const _PublicWorkersTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return _WorkerList(
      value: ref.watch(publicWorkersProvider),
      onRefresh: () => ref.refresh(publicWorkersProvider.future),
      showEmployeeId: false,
    );
  }
}

class _MasterTeamTab extends ConsumerWidget {
  const _MasterTeamTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return _WorkerList(
      value: ref.watch(masterTeamProvider),
      onRefresh: () => ref.refresh(masterTeamProvider.future),
      showEmployeeId: true,
    );
  }
}

class _WorkerList extends StatelessWidget {
  const _WorkerList({
    required this.value,
    required this.onRefresh,
    required this.showEmployeeId,
  });

  final AsyncValue<List<WorkerSummary>> value;
  final Future<void> Function() onRefresh;
  final bool showEmployeeId;

  @override
  Widget build(BuildContext context) {
    return _AsyncBody<List<WorkerSummary>>(
      value: value,
      onRefresh: onRefresh,
      builder: (workers) => ListView.separated(
        padding: const EdgeInsets.all(16),
        itemCount: workers.length,
        separatorBuilder: (_, _) => const Divider(height: 1),
        itemBuilder: (context, index) {
          final worker = workers[index];
          final details = [
            if (showEmployeeId && worker.employeeId != null) worker.employeeId!,
            if (worker.city != null) worker.city!,
            if (worker.brigadeName != null) worker.brigadeName!,
          ].join(' · ');
          return ListTile(
            onTap: showEmployeeId
                ? () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => Scaffold(
                        appBar: AppBar(title: Text(worker.fullName)),
                        body: WorkScreen(
                          path: '/master/team/${worker.id}/work-days',
                        ),
                      ),
                    ),
                  )
                : null,
            leading: CircleAvatar(
              child: Text(worker.fullName.characters.first),
            ),
            title: Text(worker.fullName),
            subtitle: details.isEmpty ? null : Text(details),
          );
        },
      ),
    );
  }
}

class _MasterObjectsTab extends ConsumerWidget {
  const _MasterObjectsTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return _AsyncBody<List<WorkObjectSummary>>(
      value: ref.watch(masterObjectsProvider),
      onRefresh: () => ref.refresh(masterObjectsProvider.future),
      builder: (objects) => ListView.builder(
        padding: const EdgeInsets.all(16),
        itemCount: objects.length,
        itemBuilder: (context, index) {
          final item = objects[index];
          return Card(
            child: ListTile(
              title: Text(item.name),
              subtitle: Text(item.address),
              trailing: Text(item.status),
            ),
          );
        },
      ),
    );
  }
}

class _AsyncBody<T> extends StatelessWidget {
  const _AsyncBody({
    required this.value,
    required this.onRefresh,
    required this.builder,
  });

  final AsyncValue<T> value;
  final Future<void> Function() onRefresh;
  final Widget Function(T value) builder;

  @override
  Widget build(BuildContext context) {
    return value.when(
      data: (data) =>
          RefreshIndicator(onRefresh: onRefresh, child: builder(data)),
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (_, _) => Center(
        child: FilledButton.icon(
          onPressed: onRefresh,
          icon: const Icon(Icons.refresh),
          label: const Text('Қайта жүктеу'),
        ),
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow({required this.label, required this.value});

  final String label;
  final String? value;

  @override
  Widget build(BuildContext context) {
    if (value == null || value!.isEmpty) return const SizedBox.shrink();
    return ListTile(
      contentPadding: EdgeInsets.zero,
      title: Text(label),
      subtitle: Text(value!),
    );
  }
}

class WebPanelNoticeScreen extends ConsumerWidget {
  const WebPanelNoticeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final role = ref.watch(authControllerProvider).value?.role;
    return Scaffold(
      appBar: AppBar(
        title: const Text('ALASH | M-1917'),
        actions: [
          IconButton(
            tooltip: 'Шығу',
            onPressed: () => ref.read(authControllerProvider.notifier).logout(),
            icon: const Icon(Icons.logout),
          ),
        ],
      ),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text(
            '${role == UserRole.boss ? 'Бастық' : 'Админ'} панелі веб-нұсқада қолжетімді.',
            textAlign: TextAlign.center,
          ),
        ),
      ),
    );
  }
}

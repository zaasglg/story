import 'dart:math';
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../auth/data/auth_repository.dart';
import 'work_screen.dart';

class CommunicationsScreen extends ConsumerWidget {
  const CommunicationsScreen({
    required this.notifications,
    this.canOpenManager = false,
    super.key,
  });
  final bool notifications;
  final bool canOpenManager;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final path = notifications ? '/me/notifications' : '/chats';
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
          data: (rows) => RefreshIndicator(
            onRefresh: () async {
              ref.invalidate(provider);
              await ref.read(provider.future);
            },
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              children: [
                if (!notifications && canOpenManager)
                  ListTile(
                    leading: const Icon(Icons.person),
                    title: const Text('Мастермен чат'),
                    onTap: () async {
                      try {
                        final response = await ref
                            .read(apiClientProvider)
                            .dio
                            .post<Map<String, dynamic>>('/chats/manager');
                        ref.invalidate(provider);
                        if (context.mounted) {
                          Navigator.of(context).push(
                            MaterialPageRoute<void>(
                              builder: (_) => ChatMessagesScreen(
                                threadId: response.data!['id'] as String,
                              ),
                            ),
                          );
                        }
                      } catch (_) {
                        if (context.mounted) {
                          ScaffoldMessenger.of(context).showSnackBar(
                            const SnackBar(
                              content: Text(
                                'Мастер тағайындалмаған немесе байланыс қолжетімсіз',
                              ),
                            ),
                          );
                        }
                      }
                    },
                  ),
                if (rows.isEmpty)
                  const ListTile(title: Text('Әзірге хабарламалар жоқ')),
                for (final row in rows)
                  ListTile(
                    leading: Icon(
                      notifications
                          ? (row['read_at'] == null
                                ? Icons.notifications_active
                                : Icons.notifications_none)
                          : Icons.chat_bubble_outline,
                    ),
                    title: Text(
                      notifications
                          ? row['title'] as String
                          : row['key'] == 'GENERAL'
                          ? 'Жалпы чат'
                          : 'Жеке чат',
                    ),
                    subtitle: notifications
                        ? Text(row['body'] as String)
                        : null,
                    onTap: () async {
                      if (notifications) {
                        try {
                          await ref
                              .read(apiClientProvider)
                              .dio
                              .post<void>(
                                '/me/notifications/${row['id']}/read',
                              );
                          ref.invalidate(provider);
                        } catch (_) {
                          if (context.mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(
                              const SnackBar(content: Text('Қайта көріңіз')),
                            );
                          }
                        }
                      } else {
                        Navigator.of(context).push(
                          MaterialPageRoute<void>(
                            builder: (_) => ChatMessagesScreen(
                              threadId: row['id'] as String,
                            ),
                          ),
                        );
                      }
                    },
                  ),
              ],
            ),
          ),
        );
  }
}

class ChatMessagesScreen extends ConsumerStatefulWidget {
  const ChatMessagesScreen({required this.threadId, super.key});
  final String threadId;

  @override
  ConsumerState<ChatMessagesScreen> createState() => _ChatMessagesScreenState();
}

class _ChatMessagesScreenState extends ConsumerState<ChatMessagesScreen>
    with WidgetsBindingObserver {
  final input = TextEditingController();
  bool sending = false;
  String? pendingId;
  String? pendingBody;
  Timer? _poller;
  bool _foreground = true;
  bool _loadingOlder = false;
  bool _hasOlder = true;
  final List<Map<String, dynamic>> _older = [];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _foreground =
        WidgetsBinding.instance.lifecycleState == null ||
        WidgetsBinding.instance.lifecycleState == AppLifecycleState.resumed;
    _poller = Timer.periodic(const Duration(seconds: 10), (_) {
      if (!mounted ||
          !_foreground ||
          ModalRoute.of(context)?.isCurrent != true) {
        return;
      }
      final provider = workDaysProvider('/chats/${widget.threadId}/messages');
      if (!ref.read(provider).isLoading) ref.invalidate(provider);
    });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _foreground = state == AppLifecycleState.resumed;
  }

  Future<void> _loadOlder(String anchorId) async {
    if (_loadingOlder) return;
    setState(() => _loadingOlder = true);
    try {
      final response = await ref
          .read(apiClientProvider)
          .dio
          .get<List<dynamic>>(
            '/chats/${widget.threadId}/messages',
            queryParameters: {'before_id': anchorId, 'limit': 50},
          );
      if (!mounted) return;
      final rows = response.data!.cast<Map<String, dynamic>>();
      setState(() {
        _older.addAll(rows);
        _hasOlder = rows.length == 50;
      });
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Ескі хабарламалар жүктелмеді. Қайта көріңіз.'),
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _loadingOlder = false);
    }
  }

  Widget _messageList(List<Map<String, dynamic>> latest) {
    final byId = <String, Map<String, dynamic>>{};
    for (final row in [..._older, ...latest]) {
      byId[row['id'] as String] = row;
    }
    _older
      ..clear()
      ..addAll(byId.values);
    final rows = byId.values.toList()
      ..sort((a, b) {
        final date = DateTime.parse(
          b['created_at'] as String,
        ).compareTo(DateTime.parse(a['created_at'] as String));
        return date != 0
            ? date
            : (a['id'] as String).compareTo(b['id'] as String);
      });
    return ListView(
      reverse: true,
      children: [
        if (rows.isEmpty)
          const ListTile(title: Text('Әзірге хабарламалар жоқ')),
        for (final message in rows)
          ListTile(
            key: ValueKey(message['id']),
            title: Text(message['body'] as String),
            subtitle: Text(message['created_at'] as String),
          ),
        if (_hasOlder && rows.length >= 50)
          TextButton(
            onPressed: _loadingOlder
                ? null
                : () => _loadOlder(rows.last['id'] as String),
            child: Text(
              _loadingOlder ? 'Жүктелуде…' : 'Ескі хабарламаларды жүктеу',
            ),
          ),
      ],
    );
  }

  @override
  void dispose() {
    _poller?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    input.dispose();
    super.dispose();
  }

  Future<void> send() async {
    final body = input.text.trim();
    if (body.isEmpty || sending) return;
    if (pendingBody != body) {
      pendingId = List.generate(
        16,
        (_) => Random.secure().nextInt(256).toRadixString(16).padLeft(2, '0'),
      ).join();
      pendingBody = body;
    }
    setState(() => sending = true);
    try {
      await ref
          .read(apiClientProvider)
          .dio
          .post<void>(
            '/chats/${widget.threadId}/messages',
            data: {'client_message_id': pendingId, 'body': body},
          );
      if (mounted) {
        ref.invalidate(workDaysProvider('/chats/${widget.threadId}/messages'));
        input.clear();
        pendingId = null;
        pendingBody = null;
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Хабарлама жіберілмеді. Қайта көріңіз.'),
          ),
        );
      }
    } finally {
      if (mounted) setState(() => sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final provider = workDaysProvider('/chats/${widget.threadId}/messages');
    return Scaffold(
      appBar: AppBar(
        title: const Text('Чат'),
        actions: [
          IconButton(
            tooltip: 'Жаңарту',
            onPressed: () => ref.invalidate(provider),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: ref
                .watch(provider)
                .when(
                  loading: () =>
                      const Center(child: CircularProgressIndicator()),
                  error: (_, _) => Center(
                    child: TextButton(
                      onPressed: () => ref.invalidate(provider),
                      child: const Text('Қайта жүктеу'),
                    ),
                  ),
                  data: _messageList,
                ),
          ),
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: input,
                      maxLength: 5000,
                      decoration: const InputDecoration(
                        hintText: 'Хабарлама',
                        counterText: '',
                      ),
                      onSubmitted: (_) => send(),
                    ),
                  ),
                  IconButton(
                    tooltip: 'Жіберу',
                    onPressed: sending ? null : send,
                    icon: const Icon(Icons.send),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../auth/data/auth_repository.dart';
import 'work_screen.dart';

class SupportScreen extends ConsumerStatefulWidget {
  const SupportScreen({super.key});

  @override
  ConsumerState<SupportScreen> createState() => _SupportScreenState();
}

class _SupportScreenState extends ConsumerState<SupportScreen> {
  final _form = GlobalKey<FormState>();
  final _subject = TextEditingController();
  final _body = TextEditingController();
  bool _sending = false;
  String? _requestId;
  String? _requestContent;
  final _provider = workDaysProvider('/me/support-tickets');

  @override
  void dispose() {
    _subject.dispose();
    _body.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    if (_sending || !_form.currentState!.validate()) return;
    final subject = _subject.text.trim();
    final body = _body.text.trim();
    final content = '$subject\u0000$body';
    if (_requestContent != content) {
      final random = Random.secure();
      _requestId = List.generate(
        16,
        (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0'),
      ).join();
      _requestContent = content;
    }
    setState(() => _sending = true);
    try {
      await ref
          .read(apiClientProvider)
          .dio
          .post<dynamic>(
            '/me/support-tickets',
            data: {
              'subject': subject,
              'body': body,
              'client_request_id': _requestId,
            },
          );
      if (!mounted) return;
      _subject.clear();
      _body.clear();
      _requestId = null;
      _requestContent = null;
      ref.invalidate(_provider);
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('Өтініш жіберілді')));
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Жіберілмеді. Қайта көріңіз.')),
        );
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: () async {
        ref.invalidate(_provider);
        await ref.read(_provider.future);
      },
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.all(20),
        children: [
          Text(
            'Қолдау қызметі',
            style: Theme.of(context).textTheme.headlineSmall,
          ),
          const SizedBox(height: 8),
          const Text(
            'Тек өз өтініштеріңіз көрсетіледі. Шұғыл жағдай үшін 112 батырмасын пайдаланыңыз.',
          ),
          const SizedBox(height: 20),
          Form(
            key: _form,
            child: Column(
              children: [
                TextFormField(
                  controller: _subject,
                  enabled: !_sending,
                  maxLength: 255,
                  decoration: const InputDecoration(labelText: 'Тақырып'),
                  validator: (value) => value == null || value.trim().isEmpty
                      ? 'Тақырыпты енгізіңіз'
                      : null,
                ),
                const SizedBox(height: 12),
                TextFormField(
                  controller: _body,
                  enabled: !_sending,
                  minLines: 3,
                  maxLines: 6,
                  maxLength: 5000,
                  decoration: const InputDecoration(labelText: 'Өтініш мәтіні'),
                  validator: (value) => value == null || value.trim().isEmpty
                      ? 'Өтініш мәтінін енгізіңіз'
                      : null,
                ),
                const SizedBox(height: 12),
                FilledButton.icon(
                  onPressed: _sending ? null : _send,
                  icon: const Icon(Icons.send_outlined),
                  label: Text(_sending ? 'Жіберілуде…' : 'Өтініш жіберу'),
                ),
              ],
            ),
          ),
          const SizedBox(height: 28),
          Text(
            'Менің өтініштерім',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const SizedBox(height: 12),
          ref
              .watch(_provider)
              .when(
                loading: () => const Center(child: CircularProgressIndicator()),
                error: (_, _) => Column(
                  children: [
                    const Text('Өтініштерді жүктеу мүмкін болмады'),
                    TextButton(
                      onPressed: () => ref.invalidate(_provider),
                      child: const Text('Қайта жүктеу'),
                    ),
                  ],
                ),
                data: (tickets) => Column(
                  children: [
                    if (tickets.isEmpty)
                      const ListTile(title: Text('Өтініштер әлі жоқ')),
                    for (final ticket in tickets)
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: Icon(
                          ticket['status'] == 'RESOLVED'
                              ? Icons.check_circle_outline
                              : Icons.pending_outlined,
                        ),
                        title: Text(ticket['subject'] as String),
                        subtitle: Text(
                          '${ticket['body']}\n${ticket['status'] == 'RESOLVED' ? 'Шешілді' : 'Қаралуда'}',
                        ),
                        isThreeLine: true,
                      ),
                  ],
                ),
              ),
        ],
      ),
    );
  }
}

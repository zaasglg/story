import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../auth/data/auth_repository.dart';

final accessDataProvider = FutureProvider.autoDispose
    .family<Map<String, dynamic>, String>((ref, path) async {
      final response = await ref
          .watch(apiClientProvider)
          .dio
          .get<Map<String, dynamic>>(path);
      return response.data!;
    });

class PassRatingScreen extends ConsumerStatefulWidget {
  const PassRatingScreen({required this.isPass, super.key});
  final bool isPass;

  @override
  ConsumerState<PassRatingScreen> createState() => _PassRatingScreenState();
}

class _PassRatingScreenState extends ConsumerState<PassRatingScreen> {
  Timer? timer;

  @override
  void initState() {
    super.initState();
    if (widget.isPass) {
      timer = Timer.periodic(const Duration(seconds: 60), (_) {
        ref.invalidate(accessDataProvider('/workers/me/pass'));
      });
    }
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final provider = accessDataProvider(
      widget.isPass ? '/workers/me/pass' : '/workers/me/rating',
    );
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
          data: (data) => RefreshIndicator(
            onRefresh: () async {
              ref.invalidate(provider);
              await ref.read(provider.future);
            },
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.all(20),
              children: widget.isPass
                  ? [
                      Text(
                        '${data['full_name']}',
                        style: Theme.of(context).textTheme.headlineSmall,
                      ),
                      ListTile(
                        title: const Text('Табель нөмірі'),
                        subtitle: Text('${data['employee_id']}'),
                      ),
                      ListTile(
                        title: Text(
                          data['status'] == 'ACTIVE'
                              ? 'Кіруге рұқсат берілген'
                              : 'Кіруге рұқсат жоқ',
                        ),
                      ),
                      if (data['status'] == 'ACTIVE')
                        Center(
                          child: Image.memory(
                            base64Decode(data['qr_png_base64'] as String),
                            width: 240,
                            height: 240,
                          ),
                        ),
                      if (data['status'] == 'BLOCKED')
                        const Text(
                          'Жарамды жұмысқа рұқсат қажет. Әкімшіге хабарласыңыз.',
                        ),
                      const SizedBox(height: 16),
                      for (final item
                          in data['issued_equipment'] as List<dynamic>)
                        ListTile(
                          title: Text('${item['name']} × ${item['quantity']}'),
                        ),
                      TextButton(
                        onPressed: () => ref.invalidate(provider),
                        child: const Text('Жаңарту'),
                      ),
                    ]
                  : [
                      Text(
                        '${data['rating']} / 5',
                        style: Theme.of(context).textTheme.headlineLarge,
                      ),
                      for (final event in data['history'] as List<dynamic>)
                        ListTile(
                          title: Text(
                            '${event['event_date']} · ${event['reason']}',
                          ),
                          trailing: Text('${event['delta']}'),
                        ),
                    ],
            ),
          ),
        );
  }
}

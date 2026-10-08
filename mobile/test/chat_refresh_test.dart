import 'dart:convert';
import 'dart:typed_data';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:m1917/features/operations/communications_screen.dart';
import 'package:m1917/features/operations/work_screen.dart';
import 'package:m1917/features/auth/data/auth_repository.dart';
import 'package:m1917/core/network/api_client.dart';
import 'package:m1917/core/storage/token_storage.dart';

class HistoryAdapter implements HttpClientAdapter {
  RequestOptions? request;
  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? stream,
    Future<void>? cancelFuture,
  ) async {
    request = options;
    return ResponseBody.fromString(
      jsonEncode([
        {
          'id': 'old',
          'body': 'Older message',
          'created_at': '2026-10-01T00:00:00Z',
        },
      ]),
      200,
      headers: {
        Headers.contentTypeHeader: ['application/json'],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

void main() {
  testWidgets('chat loads older messages using its own oldest cursor', (
    tester,
  ) async {
    final adapter = HistoryAdapter();
    final client = ApiClient(
      baseUrl: 'https://test.invalid',
      tokenStorage: MemoryTokenStorage(),
    );
    client.dio.httpClientAdapter = adapter;
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          apiClientProvider.overrideWithValue(client),
          workDaysProvider('/chats/thread/messages').overrideWith(
            (ref) async => List.generate(
              50,
              (i) => {
                'id': i.toString().padLeft(2, '0'),
                'body': 'Message $i',
                'created_at': '2026-10-06T00:00:00Z',
              },
            ),
          ),
        ],
        child: const MaterialApp(home: ChatMessagesScreen(threadId: 'thread')),
      ),
    );
    await tester.pumpAndSettle();
    final scroll = tester.state<ScrollableState>(
      find.descendant(
        of: find.byType(ListView),
        matching: find.byType(Scrollable),
      ),
    );
    for (var i = 0; i < 3; i++) {
      scroll.position.jumpTo(scroll.position.maxScrollExtent);
      await tester.pumpAndSettle();
    }
    await tester.tap(find.text('Ескі хабарламаларды жүктеу'));
    await tester.pumpAndSettle();
    expect(adapter.request!.path, '/chats/thread/messages');
    expect(adapter.request!.queryParameters['before_id'], '49');
    expect(find.text('Ескі хабарламаларды жүктеу'), findsNothing);
    await tester.pumpWidget(const SizedBox());
    client.dio.close();
  });
  testWidgets('chat polling pauses in background and stops after disposal', (
    tester,
  ) async {
    var reads = 0;
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          workDaysProvider('/chats/thread/messages').overrideWith((ref) async {
            reads++;
            return [
              {
                'id': 'message',
                'body': 'Hello',
                'created_at': '2026-10-06T00:00:00Z',
              },
            ];
          }),
        ],
        child: const MaterialApp(home: ChatMessagesScreen(threadId: 'thread')),
      ),
    );
    await tester.pumpAndSettle();
    expect(reads, 1);
    await tester.pump(const Duration(seconds: 10));
    await tester.pumpAndSettle();
    expect(reads, 2);
    expect(find.text('Hello'), findsOneWidget);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    await tester.pump(const Duration(seconds: 20));
    expect(reads, 2);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pump(const Duration(seconds: 10));
    await tester.pumpAndSettle();
    expect(reads, 3);
    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(seconds: 20));
    expect(reads, 3);
  });
}

import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:m1917/core/network/api_client.dart';
import 'package:m1917/core/storage/token_storage.dart';
import 'package:m1917/features/auth/data/auth_repository.dart';
import 'package:m1917/features/operations/emergency_screen.dart';
import 'package:m1917/features/operations/support_screen.dart';

class SupportAdapter implements HttpClientAdapter {
  final submissions = <Map<String, dynamic>>[];
  final paths = <String>[];
  bool failFirst = true;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    paths.add(options.path);
    if (options.method == 'POST') {
      submissions.add(Map<String, dynamic>.from(options.data as Map));
      return ResponseBody.fromString(
        '{}',
        failFirst && submissions.length == 1 ? 503 : 201,
        headers: {
          Headers.contentTypeHeader: ['application/json'],
        },
      );
    }
    return ResponseBody.fromString(
      jsonEncode(
        submissions.length > 1
            ? [
                {
                  'id': 'ticket',
                  'subject': 'Кесте',
                  'body': 'Көмек керек',
                  'status': 'OPEN',
                },
              ]
            : [],
      ),
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
  testWidgets(
    'support validates and retains draft and request ID across retry',
    (tester) async {
      final adapter = SupportAdapter();
      final client = ApiClient(
        baseUrl: 'https://test.invalid',
        tokenStorage: MemoryTokenStorage(),
      );
      client.dio.httpClientAdapter = adapter;
      await tester.pumpWidget(
        ProviderScope(
          overrides: [apiClientProvider.overrideWithValue(client)],
          child: const MaterialApp(home: Scaffold(body: SupportScreen())),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Өтініштер әлі жоқ'), findsOneWidget);
      await tester.tap(find.text('Өтініш жіберу'));
      await tester.pumpAndSettle();
      expect(find.text('Тақырыпты енгізіңіз'), findsOneWidget);
      expect(adapter.submissions, isEmpty);
      await tester.enterText(find.byType(TextFormField).at(0), '  Кесте  ');
      await tester.enterText(
        find.byType(TextFormField).at(1),
        '  Көмек керек  ',
      );
      await tester.ensureVisible(find.text('Өтініш жіберу'));
      await tester.tap(find.text('Өтініш жіберу'));
      await tester.pumpAndSettle();
      expect(find.text('  Кесте  '), findsOneWidget);
      expect(adapter.submissions.single['subject'], 'Кесте');
      await tester.tap(find.text('Өтініш жіберу'));
      await tester.pumpAndSettle();
      expect(adapter.submissions.length, 2);
      expect(
        adapter.submissions[0]['client_request_id'],
        adapter.submissions[1]['client_request_id'],
      );
      expect(
        adapter.paths.every((path) => path == '/me/support-tickets'),
        isTrue,
      );
      expect(
        tester
            .widget<TextFormField>(find.byType(TextFormField).first)
            .controller!
            .text,
        isEmpty,
      );
      expect(find.text('Кесте'), findsOneWidget);
    },
  );

  testWidgets(
    'emergency launch requires confirmation and cancel never launches',
    (tester) async {
      var calls = 0;
      await tester.pumpWidget(
        MaterialApp(
          home: EmergencyScreen(
            launcher: () async {
              calls++;
              return true;
            },
          ),
        ),
      );
      expect(calls, 0);
      await tester.tap(find.text('112 нөмірін ашу'));
      await tester.pumpAndSettle();
      expect(calls, 0);
      await tester.tap(find.text('Бас тарту'));
      await tester.pumpAndSettle();
      expect(calls, 0);
      await tester.tap(find.text('112 нөмірін ашу'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Телефонды ашу'));
      await tester.pumpAndSettle();
      expect(calls, 1);
    },
  );

  testWidgets('unsupported emergency handler shows copy fallback', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(home: EmergencyScreen(launcher: () async => false)),
    );
    await tester.tap(find.text('112 нөмірін ашу'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Телефонды ашу'));
    await tester.pumpAndSettle();
    expect(
      find.textContaining('Телефон қолданбасын ашу мүмкін болмады'),
      findsOneWidget,
    );
    expect(find.text('Нөмірді көшіру'), findsOneWidget);
  });

  testWidgets('emergency handler errors show fallback without propagating', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: EmergencyScreen(
          launcher: () async => throw StateError('unsupported'),
        ),
      ),
    );
    await tester.tap(find.text('112 нөмірін ашу'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Телефонды ашу'));
    await tester.pumpAndSettle();
    expect(
      find.textContaining('Телефон қолданбасын ашу мүмкін болмады'),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
  });
}

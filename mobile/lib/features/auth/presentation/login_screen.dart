import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../application/auth_controller.dart';

class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _formKey = GlobalKey<FormState>();
  final _phoneController = TextEditingController(text: '+7');
  final _codeController = TextEditingController();
  bool _codeRequested = false;
  bool _submitting = false;

  @override
  void dispose() {
    _phoneController.dispose();
    _codeController.dispose();
    super.dispose();
  }

  String? _phoneValidator(String? value) {
    if (value == null || !RegExp(r'^\+[1-9]\d{7,14}$').hasMatch(value.trim())) {
      return 'Телефонды халықаралық форматта енгізіңіз';
    }
    return null;
  }

  String? _codeValidator(String? value) {
    if (value == null || !RegExp(r'^\d{6}$').hasMatch(value.trim())) {
      return '6 таңбалы кодты енгізіңіз';
    }
    return null;
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() => _submitting = true);
    try {
      if (!_codeRequested) {
        await ref
            .read(authControllerProvider.notifier)
            .requestOtp(_phoneController.text.trim());
        if (mounted) setState(() => _codeRequested = true);
      } else {
        await ref
            .read(authControllerProvider.notifier)
            .verifyOtp(
              phone: _phoneController.text.trim(),
              code: _codeController.text.trim(),
            );
      }
    } on DioException {
      if (mounted) _showError();
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  void _showError() {
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('Сұрауды орындау мүмкін болмады. Қайталап көріңіз.'),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Card(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Form(
                    key: _formKey,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Text(
                          'ALASH | M-1917',
                          style: Theme.of(context).textTheme.headlineMedium,
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: 8),
                        const Text(
                          'Қызметкерлерді басқару жүйесі',
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: 32),
                        TextFormField(
                          controller: _phoneController,
                          enabled: !_codeRequested,
                          keyboardType: TextInputType.phone,
                          autofillHints: const [AutofillHints.telephoneNumber],
                          decoration: const InputDecoration(
                            labelText: 'Телефон нөмірі',
                          ),
                          validator: _phoneValidator,
                        ),
                        if (_codeRequested) ...[
                          const SizedBox(height: 16),
                          TextFormField(
                            controller: _codeController,
                            keyboardType: TextInputType.number,
                            autofillHints: const [AutofillHints.oneTimeCode],
                            decoration: const InputDecoration(
                              labelText: 'SMS код',
                            ),
                            validator: _codeValidator,
                          ),
                        ],
                        const SizedBox(height: 24),
                        FilledButton(
                          onPressed: _submitting ? null : _submit,
                          child: Text(_codeRequested ? 'Кіру' : 'Код алу'),
                        ),
                        if (_codeRequested)
                          TextButton(
                            onPressed: _submitting
                                ? null
                                : () => setState(() {
                                    _codeRequested = false;
                                    _codeController.clear();
                                  }),
                            child: const Text('Нөмірді өзгерту'),
                          ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:url_launcher/url_launcher.dart';

typedef EmergencyLauncher = Future<bool> Function();

Future<bool> _openEmergencyPhone() => launchUrl(
  Uri(scheme: 'tel', path: '112'),
  mode: LaunchMode.externalApplication,
);

class EmergencyScreen extends StatefulWidget {
  const EmergencyScreen({this.launcher = _openEmergencyPhone, super.key});

  final EmergencyLauncher launcher;

  @override
  State<EmergencyScreen> createState() => _EmergencyScreenState();
}

class _EmergencyScreenState extends State<EmergencyScreen> {
  bool _opening = false;
  bool _failed = false;

  Future<void> _open() async {
    if (_opening) return;
    setState(() => _opening = true);
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Телефон қолданбасын ашу?'),
        content: const Text(
          '112 нөмірі телефон қолданбасында ашылады. Бұл әрекет автоматты түрде қоңырау шалмайды; қоңырауды телефон қолданбасында растаңыз.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Бас тарту'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Телефонды ашу'),
          ),
        ],
      ),
    );
    if (!mounted) return;
    if (confirmed == true) {
      try {
        final opened = await widget.launcher();
        if (mounted) setState(() => _failed = !opened);
      } catch (_) {
        if (mounted) setState(() => _failed = true);
      }
    }
    if (mounted) setState(() => _opening = false);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Шұғыл байланыс')),
    body: ListView(
      padding: const EdgeInsets.all(24),
      children: [
        const Icon(Icons.emergency_outlined, size: 48),
        const SizedBox(height: 20),
        Text(
          '112',
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.displayLarge,
        ),
        const SizedBox(height: 16),
        const Text(
          'Шұғыл қызмет нөмірі. Қолданба хабарлама жібермейді және орналасқан жеріңізді жинамайды.',
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 28),
        FilledButton.icon(
          onPressed: _opening ? null : _open,
          icon: const Icon(Icons.phone_outlined),
          label: const Text('112 нөмірін ашу'),
        ),
        if (_failed) ...[
          const SizedBox(height: 20),
          const Text(
            'Телефон қолданбасын ашу мүмкін болмады. Нөмірді көшіріп, телефоннан өзіңіз теріңіз.',
          ),
        ],
        const SizedBox(height: 12),
        OutlinedButton.icon(
          onPressed: () async {
            await Clipboard.setData(const ClipboardData(text: '112'));
            if (context.mounted) {
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(content: Text('112 нөмірі көшірілді')),
              );
            }
          },
          icon: const Icon(Icons.copy_outlined),
          label: const Text('Нөмірді көшіру'),
        ),
      ],
    ),
  );
}

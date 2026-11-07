import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:url_launcher/url_launcher.dart';

const projectUrl = 'https://github.com/iotserver24/movie-box';
const donationUrl = 'https://ai.xibebase.in';

class DonateButton extends StatefulWidget {
  const DonateButton({super.key});

  @override
  State<DonateButton> createState() => _DonateButtonState();
}

class _DonateButtonState extends State<DonateButton> {
  bool opening = false;

  Future<void> donate() async {
    if (opening) return;
    setState(() => opening = true);
    try {
      final opened = await launchUrl(
        Uri.parse(donationUrl),
        mode: LaunchMode.externalApplication,
      );
      if (!opened) throw StateError('No browser available');
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: const Text(
              'Could not open a browser. Copy the donation link instead.',
            ),
            action: SnackBarAction(
              label: 'Copy link',
              onPressed: () async {
                await Clipboard.setData(const ClipboardData(text: donationUrl));
              },
            ),
          ),
        );
      }
    } finally {
      if (mounted) setState(() => opening = false);
    }
  }

  @override
  Widget build(BuildContext context) => FilledButton.tonalIcon(
    onPressed: opening ? null : donate,
    icon: const Icon(Icons.favorite_outline),
    label: Text(opening ? 'Opening donation page' : 'Donate'),
  );
}

class CreditsScreen extends StatelessWidget {
  const CreditsScreen({super.key});

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Credits and licenses')),
    body: SafeArea(
      child: ListView(
        padding: const EdgeInsets.all(18),
        children: [
          Text(
            'NexaStream by R3AP3R Editz',
            style: Theme.of(context).textTheme.headlineSmall,
          ),
          const SizedBox(height: 12),
          const SelectableText(projectUrl),
          const SizedBox(height: 18),
          const Text(
            'Source-available, not OSI-approved open source. '
            'Keep creator credit, never add advertising, and never sell '
            'the software or charge for access. See the full license below.',
          ),
          const SizedBox(height: 12),
          ListTile(
            leading: const Icon(Icons.description_outlined),
            title: const Text('NexaStream license'),
            subtitle: const Text('Full terms, available offline'),
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => const BundledNoticeScreen(
                  title: 'NexaStream license',
                  asset: 'assets/notices/LICENSE',
                ),
              ),
            ),
          ),
          ListTile(
            leading: const Icon(Icons.people_outline),
            title: const Text('Project acknowledgments'),
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => const BundledNoticeScreen(
                  title: 'Project acknowledgments',
                  asset: 'assets/notices/CREDITS.md',
                ),
              ),
            ),
          ),
          ListTile(
            leading: const Icon(Icons.article_outlined),
            title: const Text('Dependency licenses'),
            subtitle: const Text('Third-party packages retain their own terms'),
            onTap: () => showLicensePage(
              context: context,
              applicationName: 'NexaStream 2 Downloader',
              applicationLegalese: 'NexaStream by R3AP3R Editz',
            ),
          ),
        ],
      ),
    ),
  );
}

class BundledNoticeScreen extends StatefulWidget {
  final String title;
  final String asset;
  const BundledNoticeScreen({
    super.key,
    required this.title,
    required this.asset,
  });

  @override
  State<BundledNoticeScreen> createState() => _BundledNoticeScreenState();
}

class _BundledNoticeScreenState extends State<BundledNoticeScreen> {
  late final Future<String> notice = rootBundle.loadString(widget.asset);

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: Text(widget.title)),
    body: FutureBuilder<String>(
      future: notice,
      builder: (context, snapshot) {
        if (snapshot.hasError) {
          return const Padding(
            padding: EdgeInsets.all(18),
            child: Text(
              'This build is missing its notice. '
              'Get a complete build before redistributing it.',
            ),
          );
        }
        if (!snapshot.hasData) {
          return const Center(child: CircularProgressIndicator());
        }
        return SingleChildScrollView(
          padding: const EdgeInsets.all(18),
          child: SelectableText(snapshot.data!),
        );
      },
    ),
  );
}

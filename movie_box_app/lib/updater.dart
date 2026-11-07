import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

class AppUpdateInfo {
  final String tagName;
  final String name;
  final String htmlUrl;
  final String? downloadUrl;
  final String body;

  AppUpdateInfo({
    required this.tagName,
    required this.name,
    required this.htmlUrl,
    this.downloadUrl,
    required this.body,
  });
}

class UpdateChecker {
  static const String currentVersion = '1.0.5';
  static const String repoUrl = 'https://github.com/JOKERPADDY/movie-box';
  static const String latestReleaseApi =
      'https://api.github.com/repos/JOKERPADDY/movie-box/releases/latest';

  static Future<AppUpdateInfo?> checkForUpdate() async {
    try {
      final response = await http.get(
        Uri.parse(latestReleaseApi),
        headers: {'Accept': 'application/vnd.github+json'},
      ).timeout(const Duration(seconds: 8));

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body) as Map<String, dynamic>;
        final tagName = (data['tag_name'] as String? ?? '').trim();
        final name = (data['name'] as String? ?? '').trim();
        final htmlUrl = (data['html_url'] as String? ?? repoUrl).trim();
        final body = (data['body'] as String? ?? '').trim();

        String? downloadUrl;
        final assets = data['assets'] as List<dynamic>?;
        if (assets != null) {
          for (final asset in assets) {
            final assetName = (asset['name'] as String? ?? '').toLowerCase();
            if (assetName.endsWith('.apk')) {
              downloadUrl = asset['browser_download_url'] as String?;
              break;
            }
          }
        }

        if (isVersionNewer(tagName, currentVersion)) {
          return AppUpdateInfo(
            tagName: tagName,
            name: name.isEmpty ? tagName : name,
            htmlUrl: htmlUrl,
            downloadUrl: downloadUrl,
            body: body,
          );
        }
      }
    } catch (e) {
      debugPrint('Update check error: $e');
    }
    return null;
  }

  static bool isVersionNewer(String latestTag, String current) {
    final cleanLatest = latestTag.replaceAll(RegExp(r'^[vV]'), '').trim();
    final cleanCurrent = current.replaceAll(RegExp(r'^[vV]'), '').trim();
    if (cleanLatest.isEmpty) return false;

    List<int> parseVersion(String v) {
      final mainPart = v.split('+').first;
      final parts = mainPart.split('.');
      return parts.map((p) => int.tryParse(p) ?? 0).toList();
    }

    final latestParts = parseVersion(cleanLatest);
    final currentParts = parseVersion(cleanCurrent);

    for (var i = 0; i < 3; i++) {
      final l = i < latestParts.length ? latestParts[i] : 0;
      final c = i < currentParts.length ? currentParts[i] : 0;
      if (l > c) return true;
      if (l < c) return false;
    }
    return false;
  }

  static Future<void> showUpdateDialog(BuildContext context, AppUpdateInfo info) async {
    return showDialog<void>(
      context: context,
      barrierDismissible: true,
      builder: (context) {
        final theme = Theme.of(context);
        return AlertDialog(
          backgroundColor: const Color(0xFF1F1F1F),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
            side: const BorderSide(color: Color(0xFFE50914), width: 1.5),
          ),
          title: Row(
            children: [
              const Icon(Icons.system_update, color: Color(0xFFE50914)),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  'Update Available (${info.tagName})',
                  style: theme.textTheme.titleMedium?.copyWith(
                    color: Colors.white,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
            ],
          ),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'A new version of NexaStream 2 Downloader is available on GitHub!',
                style: theme.textTheme.bodyMedium?.copyWith(color: Colors.white70),
              ),
              if (info.body.isNotEmpty) ...[
                const SizedBox(height: 12),
                Container(
                  padding: const EdgeInsets.all(10),
                  constraints: const BoxConstraints(maxHeight: 120),
                  decoration: BoxDecoration(
                    color: Colors.black26,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: SingleChildScrollView(
                    child: Text(
                      info.body,
                      style: theme.textTheme.bodySmall?.copyWith(color: Colors.white60),
                    ),
                  ),
                ),
              ],
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('Later', style: TextStyle(color: Colors.white54)),
            ),
            ElevatedButton(
              style: ElevatedButton.styleFrom(
                backgroundColor: const Color(0xFFE50914),
                foregroundColor: Colors.white,
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(8),
                ),
              ),
              onPressed: () async {
                Navigator.of(context).pop();
                final urlString = info.downloadUrl ?? info.htmlUrl;
                final uri = Uri.parse(urlString);
                if (await canLaunchUrl(uri)) {
                  await launchUrl(uri, mode: LaunchMode.externalApplication);
                }
              },
              child: const Text('Update Now', style: TextStyle(fontWeight: FontWeight.bold)),
            ),
          ],
        );
      },
    );
  }
}

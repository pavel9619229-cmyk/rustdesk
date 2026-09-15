import 'package:url_launcher/url_launcher.dart';

bool isMashaAllowedExternalUri(Uri uri) {
  final scheme = uri.scheme.toLowerCase();
  if (scheme == 'file') return true;
  if (scheme != 'http' && scheme != 'https') return false;

  final host = uri.host.toLowerCase();
  if (host == '77.222.38.70' ||
      host == 'agentmasha.ru' ||
      host.endsWith('.agentmasha.ru')) {
    return true;
  }
  if (host == 'yookassa.ru' ||
      host.endsWith('.yookassa.ru') ||
      host == 'yoomoney.ru' ||
      host.endsWith('.yoomoney.ru')) {
    return true;
  }
  return false;
}

Future<bool> launchMashaExternalUri(Uri uri,
    {LaunchMode mode = LaunchMode.platformDefault}) {
  if (!isMashaAllowedExternalUri(uri)) {
    return Future<bool>.value(false);
  }
  return launchUrl(uri, mode: mode);
}

Future<bool> launchMashaExternalUrlString(String value,
    {LaunchMode mode = LaunchMode.platformDefault}) {
  final uri = Uri.tryParse(value);
  if (uri == null) return Future<bool>.value(false);
  return launchMashaExternalUri(uri, mode: mode);
}

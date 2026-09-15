import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_hbb/masha_network_policy.dart';

void main() {
  test('allows only Masha and Russian payment web destinations', () {
    expect(
        isMashaAllowedExternalUri(Uri.parse('https://agentmasha.ru')), isTrue);
    expect(
        isMashaAllowedExternalUri(
            Uri.parse('https://api.agentmasha.ru/health')),
        isTrue);
    expect(
        isMashaAllowedExternalUri(
            Uri.parse('https://77.222.38.70:8443/v1/access/status')),
        isTrue);
    expect(isMashaAllowedExternalUri(Uri.parse('https://yookassa.ru/pay')),
        isTrue);
    expect(
        isMashaAllowedExternalUri(
            Uri.parse('https://checkout.yookassa.ru/pay')),
        isTrue);
    expect(isMashaAllowedExternalUri(Uri.parse('https://yoomoney.ru/checkout')),
        isTrue);
    expect(isMashaAllowedExternalUri(Uri.file(r'C:\Temp\file.txt')), isTrue);
  });

  test('blocks foreign and deceptive destinations', () {
    expect(isMashaAllowedExternalUri(Uri.parse('https://github.com/openai')),
        isFalse);
    expect(
        isMashaAllowedExternalUri(Uri.parse('https://rustdesk.com')), isFalse);
    expect(isMashaAllowedExternalUri(Uri.parse('https://api.telegram.org')),
        isFalse);
    expect(
        isMashaAllowedExternalUri(
            Uri.parse('https://agentmasha.ru.evil.example')),
        isFalse);
    expect(isMashaAllowedExternalUri(Uri.parse('ftp://agentmasha.ru/file')),
        isFalse);
  });
}

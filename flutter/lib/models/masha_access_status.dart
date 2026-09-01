import 'dart:convert';

import 'package:http/http.dart' as http;

const mashaAccessStatusEndpoint = 'https://77.222.38.70:8443/v1/access/status';
const mashaPaymentsCreateEndpoint =
    'https://77.222.38.70:8443/v1/payments/create';
const mashaPaymentsSyncEndpoint = 'https://77.222.38.70:8443/v1/payments/sync';

abstract class MashaAccessStatusSource {
  Future<MashaAccessStatus> fetch(String operatorId);

  Future<MashaPaymentOrder> createPayment(String operatorId) =>
      Future.error(UnsupportedError('payments are not supported'));

  Future<MashaPaymentOrder> syncPayment(String paymentOrderId) =>
      Future.error(UnsupportedError('payments are not supported'));

  void close() {}
}

class MashaAccessStatusClient implements MashaAccessStatusSource {
  MashaAccessStatusClient({http.Client? client, Uri? endpoint})
      : _client = client ?? http.Client(),
        endpoint = endpoint ?? Uri.parse(mashaAccessStatusEndpoint);

  final http.Client _client;
  final Uri endpoint;

  @override
  Future<MashaAccessStatus> fetch(String operatorId) async {
    final normalizedId = operatorId.trim();
    if (normalizedId.isEmpty) {
      throw const FormatException('operator id is empty');
    }
    final uri =
        endpoint.replace(queryParameters: {'operator_id': normalizedId});
    final response =
        await _client.get(uri).timeout(const Duration(seconds: 10));
    if (response.statusCode != 200) {
      throw StateError('access status HTTP ${response.statusCode}');
    }
    final decoded = jsonDecode(utf8.decode(response.bodyBytes));
    if (decoded is! Map<String, dynamic>) {
      throw const FormatException('access status is not an object');
    }
    return MashaAccessStatus.fromJson(decoded);
  }

  @override
  Future<MashaPaymentOrder> createPayment(String operatorId) async {
    final normalizedId = operatorId.trim();
    if (normalizedId.isEmpty) {
      throw const FormatException('operator id is empty');
    }
    final response = await _client
        .post(
          Uri.parse(mashaPaymentsCreateEndpoint),
          headers: const {'Content-Type': 'application/json'},
          body: jsonEncode({'operator_id': normalizedId}),
        )
        .timeout(const Duration(seconds: 15));
    final decoded = jsonDecode(utf8.decode(response.bodyBytes));
    if (response.statusCode != 200 || decoded is! Map<String, dynamic>) {
      throw StateError('payment create HTTP ${response.statusCode}');
    }
    if (decoded['ok'] != true) {
      throw StateError(
          _paymentString(decoded['reason'], fallback: 'payment failed'));
    }
    return MashaPaymentOrder.fromJson(decoded);
  }

  @override
  Future<MashaPaymentOrder> syncPayment(String paymentOrderId) async {
    final normalizedId = paymentOrderId.trim();
    if (normalizedId.isEmpty) {
      throw const FormatException('payment order id is empty');
    }
    final response = await _client
        .post(
          Uri.parse(mashaPaymentsSyncEndpoint),
          headers: const {'Content-Type': 'application/json'},
          body: jsonEncode({'payment_order_id': normalizedId}),
        )
        .timeout(const Duration(seconds: 15));
    final decoded = jsonDecode(utf8.decode(response.bodyBytes));
    if (response.statusCode != 200 || decoded is! Map<String, dynamic>) {
      throw StateError('payment sync HTTP ${response.statusCode}');
    }
    final payment = decoded['payment'];
    if (decoded['ok'] != true || payment is! Map<String, dynamic>) {
      throw StateError(
          _paymentString(decoded['reason'], fallback: 'payment sync failed'));
    }
    return MashaPaymentOrder.fromJson(payment);
  }

  @override
  void close() => _client.close();
}

class MashaPaymentOrder {
  const MashaPaymentOrder({
    required this.paymentOrderId,
    required this.status,
    required this.amountMinor,
    required this.currency,
    required this.reused,
    this.providerPaymentId,
    this.confirmationUrl,
  });

  factory MashaPaymentOrder.fromJson(Map<String, dynamic> json) =>
      MashaPaymentOrder(
        paymentOrderId: _paymentString(json['payment_order_id']),
        providerPaymentId: _paymentNullableString(json['provider_payment_id']),
        status: _paymentString(json['status']),
        amountMinor: _paymentInteger(json['amount_minor']) ?? 0,
        currency: _paymentString(json['currency'], fallback: 'RUB'),
        confirmationUrl: _paymentNullableString(json['confirmation_url']),
        reused: json['reused'] == true,
      );

  final String paymentOrderId;
  final String? providerPaymentId;
  final String status;
  final int amountMinor;
  final String currency;
  final String? confirmationUrl;
  final bool reused;
}

int? _paymentInteger(Object? value) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  return int.tryParse(value?.toString() ?? '');
}

String _paymentString(Object? value, {String fallback = ''}) {
  final result = value?.toString() ?? '';
  return result.isEmpty ? fallback : result;
}

String? _paymentNullableString(Object? value) {
  if (value == null) return null;
  final text = value.toString();
  return text.isEmpty ? null : text;
}

class MashaAccessStatus {
  const MashaAccessStatus({
    required this.allowed,
    required this.reason,
    required this.policyMode,
    required this.grantSource,
    required this.billingStatus,
    required this.amountDueMinor,
    required this.currency,
    required this.warning10Minutes,
    required this.billableSeconds,
    required this.serverTime,
    this.dueAt,
    this.graceUntil,
    this.warningAt,
    this.secondsUntilBlock,
    this.rateMinorPerHour,
    this.paymentDueSeconds,
    this.graceSeconds,
    this.warningSeconds,
  });

  factory MashaAccessStatus.fromJson(Map<String, dynamic> json) {
    return MashaAccessStatus(
      allowed: json['allowed'] == true,
      reason: _string(json['reason']),
      policyMode: _string(json['policy_mode']),
      grantSource: _string(json['grant_source']),
      billingStatus: _string(json['billing_status']),
      amountDueMinor: _integer(json['amount_due_minor']) ?? 0,
      currency: _string(json['currency'], fallback: 'RUB'),
      warning10Minutes: json['warning_10_minutes'] == true,
      billableSeconds: _integer(json['billable_seconds']) ?? 0,
      serverTime: _integer(json['server_time']) ?? 0,
      dueAt: _integer(json['due_at']),
      graceUntil: _integer(json['grace_until']),
      warningAt: _integer(json['warning_at']),
      secondsUntilBlock: _integer(json['seconds_until_block']),
      rateMinorPerHour: _integer(json['rate_minor_per_hour']),
      paymentDueSeconds: _integer(json['payment_due_seconds']),
      graceSeconds: _integer(json['grace_seconds']),
      warningSeconds: _integer(json['warning_seconds']),
    );
  }

  final bool allowed;
  final String reason;
  final String policyMode;
  final String grantSource;
  final String billingStatus;
  final int amountDueMinor;
  final String currency;
  final bool warning10Minutes;
  final int billableSeconds;
  final int serverTime;
  final int? dueAt;
  final int? graceUntil;
  final int? warningAt;
  final int? secondsUntilBlock;
  final int? rateMinorPerHour;
  final int? paymentDueSeconds;
  final int? graceSeconds;
  final int? warningSeconds;

  String get tariffLabel => rateMinorPerHour == null
      ? 'Не назначен'
      : '${money(rateMinorPerHour!)} ₽/час';

  String get debtLabel => '${money(amountDueMinor)} ₽';

  String get sourceLabel {
    switch (grantSource) {
      case 'postpaid_account':
        return 'Постоплата';
      case 'promo':
        return 'Промокод';
      case 'ad_reward':
        return 'Реклама';
      case 'admin':
        return 'Администратор';
      case 'payment':
        return 'Оплата';
      case 'trial':
        return 'Пробный доступ';
      default:
        return grantSource.isEmpty ? 'Нет действующего права' : grantSource;
    }
  }

  String get accessLabel {
    if (!allowed) {
      if (reason == 'payment_required' || billingStatus == 'blocked') {
        return 'Заблокировано';
      }
      return 'Доступ запрещён';
    }
    if (billingStatus == 'blocked') {
      return 'Доступ разрешён по альтернативному праву';
    }
    if (billingStatus == 'overdue') {
      return 'Просрочено, действует grace period';
    }
    if (billingStatus == 'payment_due') {
      return 'Ожидается оплата';
    }
    return 'Доступ разрешён';
  }

  static String money(int minor) {
    final sign = minor < 0 ? '-' : '';
    final absolute = minor.abs();
    final rubles = absolute ~/ 100;
    final kopecks = absolute % 100;
    return kopecks == 0
        ? '$sign$rubles'
        : '$sign$rubles,${kopecks.toString().padLeft(2, '0')}';
  }

  static int? _integer(Object? value) {
    if (value is int) return value;
    if (value is num) return value.toInt();
    return int.tryParse(value?.toString() ?? '');
  }

  static String _string(Object? value, {String fallback = ''}) {
    final result = value?.toString() ?? '';
    return result.isEmpty ? fallback : result;
  }
}

import 'package:flutter/material.dart';
import 'package:flutter_hbb/desktop/widgets/access_payment_panel.dart';
import 'package:flutter_hbb/models/masha_access_status.dart';
import 'package:flutter_test/flutter_test.dart';

class _FakeSource implements MashaAccessStatusSource {
  _FakeSource(this.status);

  final MashaAccessStatus status;
  int calls = 0;

  @override
  Future<MashaAccessStatus> fetch(String operatorId) async {
    expect(operatorId, 'operator-01');
    calls += 1;
    return status;
  }

  @override
  void close() {}
}

Map<String, dynamic> _json({
  bool allowed = true,
  String billingStatus = 'overdue',
  String grantSource = 'postpaid_account',
}) {
  return {
    'allowed': allowed,
    'reason': allowed ? 'allowed' : 'payment_required',
    'policy_mode': 'postpaid',
    'grant_source': grantSource,
    'billing_status': billingStatus,
    'amount_due_minor': 100,
    'currency': 'RUB',
    'due_at': 2000000000,
    'grace_until': 2000000600,
    'warning_at': 2000000000,
    'warning_10_minutes': true,
    'seconds_until_block': 600,
    'rate_minor_per_hour': 100,
    'payment_due_seconds': 86400,
    'grace_seconds': 3600,
    'warning_seconds': 600,
    'billable_seconds': 3600,
    'server_time': 2000000000,
  };
}

void main() {
  test('parses and formats server billing values', () {
    final status = MashaAccessStatus.fromJson(_json());

    expect(status.tariffLabel, '1 ₽/час');
    expect(status.debtLabel, '1 ₽');
    expect(status.accessLabel, 'Просрочено, действует grace period');
    expect(status.sourceLabel, 'Постоплата');
    expect(status.paymentDueSeconds, 86400);
    expect(status.graceSeconds, 3600);
    expect(status.warningSeconds, 600);
    expect(status.billableSeconds, 3600);
  });

  test('shows an alternative grant while postpaid billing is blocked', () {
    final status = MashaAccessStatus.fromJson(_json(
      billingStatus: 'blocked',
      grantSource: 'promo',
    ));

    expect(status.allowed, isTrue);
    expect(status.accessLabel, 'Доступ разрешён по альтернативному праву');
    expect(status.sourceLabel, 'Промокод');
  });

  testWidgets('renders access and payment fields from the server',
      (tester) async {
    final source = _FakeSource(MashaAccessStatus.fromJson(_json()));

    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SizedBox(
          width: 430,
          child: MashaAccessPaymentPanel(
            operatorIdLoader: () async => 'operator-01',
            source: source,
            refreshInterval: const Duration(days: 1),
          ),
        ),
      ),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Доступ и оплата'), findsOneWidget);
    expect(find.text('Тариф'), findsOneWidget);
    expect(find.text('1 ₽/час'), findsOneWidget);
    expect(find.text('Задолженность'), findsOneWidget);
    expect(find.text('1 ₽'), findsOneWidget);
    expect(find.text('Срок оплаты'), findsOneWidget);
    expect(find.text('Grace period'), findsOneWidget);
    expect(find.text('Предупреждение'), findsOneWidget);
    expect(find.text('Просрочено, действует grace period'), findsOneWidget);
    expect(find.text('Постоплата'), findsOneWidget);
    expect(find.byKey(const Key('masha-access-warning')), findsOneWidget);
    expect(source.calls, 1);

    await tester.pumpWidget(const SizedBox.shrink());
  });
}

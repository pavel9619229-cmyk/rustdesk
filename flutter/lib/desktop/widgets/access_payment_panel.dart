import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_hbb/models/masha_access_status.dart';

class MashaAccessPaymentPanel extends StatefulWidget {
  const MashaAccessPaymentPanel({
    super.key,
    required this.operatorIdLoader,
    this.source,
    this.refreshInterval = const Duration(seconds: 30),
  });

  final Future<String> Function() operatorIdLoader;
  final MashaAccessStatusSource? source;
  final Duration refreshInterval;

  @override
  State<MashaAccessPaymentPanel> createState() =>
      _MashaAccessPaymentPanelState();
}

class _MashaAccessPaymentPanelState extends State<MashaAccessPaymentPanel> {
  late final MashaAccessStatusSource _source;
  late final bool _ownsSource;
  Timer? _refreshTimer;
  Timer? _clockTimer;
  MashaAccessStatus? _status;
  DateTime? _receivedAt;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _ownsSource = widget.source == null;
    _source = widget.source ?? MashaAccessStatusClient();
    WidgetsBinding.instance.addPostFrameCallback((_) => _refresh());
    _refreshTimer = Timer.periodic(widget.refreshInterval, (_) => _refresh());
    _clockTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted && _status?.graceUntil != null) setState(() {});
    });
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    _clockTimer?.cancel();
    if (_ownsSource) _source.close();
    super.dispose();
  }

  Future<void> _refresh() async {
    if (!mounted) return;
    try {
      final operatorId = await widget.operatorIdLoader();
      final status = await _source.fetch(operatorId);
      if (!mounted) return;
      setState(() {
        _status = status;
        _receivedAt = DateTime.now();
        _error = null;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = 'Не удалось получить статус с сервера';
        _loading = false;
      });
    }
  }

  int? get _secondsUntilBlock {
    final status = _status;
    final receivedAt = _receivedAt;
    if (status == null) return null;
    if (status.graceUntil != null && receivedAt != null) {
      final elapsed = DateTime.now().difference(receivedAt).inSeconds;
      final serverNow = status.serverTime + (elapsed > 0 ? elapsed : 0);
      final remaining = status.graceUntil! - serverNow;
      return remaining > 0 ? remaining : 0;
    }
    return status.secondsUntilBlock;
  }

  @override
  Widget build(BuildContext context) {
    final status = _status;
    final accent = _accentColor(context, status);
    return Container(
      constraints: const BoxConstraints(maxWidth: 430),
      padding: const EdgeInsets.fromLTRB(16, 13, 16, 14),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        borderRadius: BorderRadius.circular(13),
        border: Border.all(color: accent.withOpacity(0.65)),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              Icon(Icons.account_balance_wallet_outlined,
                  size: 19, color: accent),
              const SizedBox(width: 8),
              const Expanded(
                child: Text(
                  'Доступ и оплата',
                  style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
                ),
              ),
              IconButton(
                key: const Key('masha-access-refresh'),
                tooltip: 'Обновить',
                visualDensity: VisualDensity.compact,
                onPressed: _loading ? null : _refresh,
                icon: const Icon(Icons.refresh, size: 18),
              ),
            ],
          ),
          if (_loading && status == null)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 38),
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          else if (_error != null && status == null)
            _errorView(accent)
          else if (status != null)
            _statusView(context, status, accent),
        ],
      ),
    );
  }

  Widget _errorView(Color accent) {
    return Padding(
      padding: const EdgeInsets.only(top: 16, bottom: 22),
      child: Row(
        children: [
          Icon(Icons.cloud_off_outlined, color: accent, size: 18),
          const SizedBox(width: 8),
          Expanded(child: Text(_error!)),
        ],
      ),
    );
  }

  Widget _statusView(
      BuildContext context, MashaAccessStatus status, Color accent) {
    final remaining = _secondsUntilBlock;
    return Column(
      children: [
        if (status.warning10Minutes)
          Container(
            key: const Key('masha-access-warning'),
            width: double.infinity,
            margin: const EdgeInsets.only(bottom: 8),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
            decoration: BoxDecoration(
              color: Colors.orange.withOpacity(0.14),
              borderRadius: BorderRadius.circular(7),
            ),
            child: Text(
              'Предупреждение: до блокировки ${_duration(remaining)}',
              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
            ),
          ),
        _row('Тариф', status.tariffLabel),
        _row('Задолженность', status.debtLabel),
        _row('Срок оплаты', _dateTime(status.dueAt)),
        _row(
          'Grace period',
          '${_duration(status.graceSeconds)}; до блокировки ${_duration(remaining)}',
        ),
        _row(
          'Предупреждение',
          status.warning10Minutes
              ? 'Активно'
              : 'За ${_duration(status.warningSeconds)} до блокировки',
        ),
        _row('Текущий статус', status.accessLabel,
            valueColor: accent, valueKey: const Key('masha-access-status')),
        _row('Источник права', status.sourceLabel),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.only(top: 7),
            child: Text(
              'Показаны последние полученные данные',
              style: TextStyle(
                color: Theme.of(context).colorScheme.error,
                fontSize: 10.5,
              ),
            ),
          ),
      ],
    );
  }

  Widget _row(String label, String value, {Color? valueColor, Key? valueKey}) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2.5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 118,
            child: Text(label,
                style: const TextStyle(fontSize: 11.5, color: Colors.grey)),
          ),
          Expanded(
            child: Text(
              value,
              key: valueKey,
              textAlign: TextAlign.right,
              style: TextStyle(
                fontSize: 11.5,
                fontWeight: FontWeight.w500,
                color: valueColor,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Color _accentColor(BuildContext context, MashaAccessStatus? status) {
    if (status == null) return Theme.of(context).colorScheme.primary;
    if (!status.allowed) return Colors.red;
    if (status.billingStatus == 'blocked' ||
        status.warning10Minutes ||
        status.billingStatus == 'overdue') {
      return Colors.orange.shade800;
    }
    return Colors.green.shade700;
  }

  String _dateTime(int? unixSeconds) {
    if (unixSeconds == null) return 'Не назначен';
    final value = DateTime.fromMillisecondsSinceEpoch(
      unixSeconds * 1000,
      isUtc: true,
    ).toLocal();
    String two(int number) => number.toString().padLeft(2, '0');
    return '${two(value.day)}.${two(value.month)}.${value.year} '
        '${two(value.hour)}:${two(value.minute)}';
  }

  String _duration(int? seconds) {
    if (seconds == null) return 'не назначено';
    if (seconds <= 0) return '0 мин';
    final days = seconds ~/ 86400;
    final hours = (seconds % 86400) ~/ 3600;
    final minutes = max(1, (seconds % 3600) ~/ 60);
    final parts = <String>[];
    if (days > 0) parts.add('$days дн.');
    if (hours > 0) parts.add('$hours ч');
    if (days == 0 && minutes > 0) parts.add('$minutes мин');
    return parts.join(' ');
  }
}

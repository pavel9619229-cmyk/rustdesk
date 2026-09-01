import importlib.util
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

SERVICE_FILE = Path(__file__).resolve().parents[1] / 'masha_auth.py'

class YooKassaStage21Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.saved_env = dict(os.environ)
        os.environ['MASHA_AUTH_DIR'] = self.temporary.name
        os.environ['YOOKASSA_SHOP_ID'] = 'test-shop'
        os.environ['YOOKASSA_SECRET_KEY'] = 'test-secret'
        os.environ['YOOKASSA_RETURN_URL'] = 'https://pay.example.ru/return'
        os.environ['YOOKASSA_RECEIPT_MODE'] = 'none'
        spec = importlib.util.spec_from_file_location('masha_auth_yk_' + self._testMethodName, SERVICE_FILE)
        self.auth = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.auth)
        self.auth.init_db()
        self.counter = 0
        self.created = {}

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved_env)
        self.temporary.cleanup()

    def set_debt(self, operator_id='operator-pay', seconds=3600, blocked=True):
        self.auth.enable_postpaid(operator_id)
        now = int(self.auth.time.time())
        amount = seconds * 100 // 3600
        with self.auth.dbc() as connection:
            connection.execute(
                "UPDATE billing_accounts SET billing_status=?,amount_due_minor=?,"
                "billable_seconds=?,due_at=?,grace_until=?,blocked_at=?,updated_at=? "
                "WHERE operator_id=?",
                ('blocked' if blocked else 'payment_due', amount, seconds,
                 now - 7200, now - 3600 if blocked else now + 3600,
                 now - 3600 if blocked else None, now, operator_id),
            )
        return amount

    def fake_create_api(self, method, path, payload=None, idempotence_key=None):
        self.assertEqual((method, path), ('POST', '/payments'))
        self.counter += 1
        provider_id = f'yk-payment-{self.counter}'
        result = {
            'id': provider_id,
            'status': 'pending',
            'paid': False,
            'amount': dict(payload['amount']),
            'confirmation': {'type': 'redirect', 'confirmation_url': f'https://yookassa.test/{provider_id}'},
            'metadata': dict(payload['metadata']),
        }
        self.created[provider_id] = result
        return result

    def succeeded(self, provider_id, amount_value=None, metadata=None):
        base = self.created[provider_id]
        return {
            'id': provider_id,
            'status': 'succeeded',
            'paid': True,
            'amount': {
                'value': amount_value or base['amount']['value'],
                'currency': base['amount']['currency'],
            },
            'metadata': dict(metadata or base['metadata']),
        }

    def webhook(self, provider_id, event='payment.succeeded'):
        return {'type': 'notification', 'event': event, 'object': {'id': provider_id, 'status': event.split('.')[-1]}}

    def test_create_payment_uses_exact_debt_and_reuses_pending_order(self):
        self.set_debt(seconds=3600)
        with mock.patch.object(self.auth, 'yookassa_api', side_effect=self.fake_create_api) as api:
            first = self.auth.create_yookassa_payment({'operator_id': 'operator-pay'})
            second = self.auth.create_yookassa_payment({'operator_id': 'operator-pay'})
        self.assertFalse(first['reused'])
        self.assertTrue(second['reused'])
        self.assertEqual(first['amount_minor'], 100)
        self.assertEqual(first['confirmation_url'], 'https://yookassa.test/yk-payment-1')
        self.assertEqual(api.call_count, 1)
        sent = api.call_args.args[2]
        self.assertEqual(sent['amount'], {'value': '1.00', 'currency': 'RUB'})
        self.assertTrue(sent['capture'])
        self.assertEqual(sent['metadata']['operator_id'], 'operator-pay')

    def test_create_payment_fails_closed_without_credentials(self):
        self.set_debt()
        os.environ.pop('YOOKASSA_SECRET_KEY')
        with self.assertRaises(self.auth.PaymentError) as ctx:
            self.auth.create_yookassa_payment({'operator_id': 'operator-pay'})
        self.assertEqual((ctx.exception.http_status, ctx.exception.reason), (503, 'provider_not_configured'))
        with self.auth.dbc() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM payment_orders').fetchone()[0], 0)

    def test_verified_success_webhook_settles_and_restores_access(self):
        self.set_debt()
        with mock.patch.object(self.auth, 'yookassa_api', side_effect=self.fake_create_api):
            order = self.auth.create_yookassa_payment({'operator_id': 'operator-pay'})
        provider_id = order['provider_payment_id']
        with mock.patch.object(self.auth, 'yookassa_get_payment', return_value=self.succeeded(provider_id)) as verify:
            result = self.auth.process_yookassa_webhook(self.webhook(provider_id))
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(result['result']['reason'], 'payment_applied')
        status = self.auth.access_status('operator-pay')
        self.assertTrue(status['allowed'])
        self.assertEqual((status['billing_status'], status['amount_due_minor'], status['billable_seconds']), ('current', 0, 0))
        with self.auth.dbc() as connection:
            event = connection.execute('SELECT processing_status FROM payment_events').fetchone()[0]
            settled = connection.execute('SELECT status,settled_at FROM payment_orders').fetchone()
        self.assertEqual(event, 'applied')
        self.assertEqual(settled['status'], 'succeeded')
        self.assertIsNotNone(settled['settled_at'])

    def test_duplicate_webhook_is_idempotent(self):
        self.set_debt()
        with mock.patch.object(self.auth, 'yookassa_api', side_effect=self.fake_create_api):
            order = self.auth.create_yookassa_payment({'operator_id': 'operator-pay'})
        provider = self.succeeded(order['provider_payment_id'])
        with mock.patch.object(self.auth, 'yookassa_get_payment', return_value=provider) as verify:
            first = self.auth.process_yookassa_webhook(self.webhook(order['provider_payment_id']))
            second = self.auth.process_yookassa_webhook(self.webhook(order['provider_payment_id']))
        self.assertFalse(first['duplicate'])
        self.assertTrue(second['duplicate'])
        self.assertEqual(verify.call_count, 1)
        with self.auth.dbc() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM payment_events').fetchone()[0], 1)
            account = connection.execute('SELECT amount_due_minor,billable_seconds FROM billing_accounts WHERE operator_id=?', ('operator-pay',)).fetchone()
        self.assertEqual(tuple(account), (0, 0))

    def test_spoofed_success_body_does_not_settle_when_provider_is_pending(self):
        self.set_debt()
        with mock.patch.object(self.auth, 'yookassa_api', side_effect=self.fake_create_api):
            order = self.auth.create_yookassa_payment({'operator_id': 'operator-pay'})
        pending = dict(self.created[order['provider_payment_id']])
        with mock.patch.object(self.auth, 'yookassa_get_payment', return_value=pending):
            result = self.auth.process_yookassa_webhook(self.webhook(order['provider_payment_id']))
        self.assertEqual(result['result']['reason'], 'payment_not_succeeded')
        status = self.auth.access_status('operator-pay')
        self.assertFalse(status['allowed'])
        self.assertEqual((status['billing_status'], status['amount_due_minor']), ('blocked', 100))

    def test_amount_mismatch_is_rejected_without_settlement(self):
        self.set_debt()
        with mock.patch.object(self.auth, 'yookassa_api', side_effect=self.fake_create_api):
            order = self.auth.create_yookassa_payment({'operator_id': 'operator-pay'})
        bad = self.succeeded(order['provider_payment_id'], amount_value='2.00')
        with mock.patch.object(self.auth, 'yookassa_get_payment', return_value=bad):
            result = self.auth.process_yookassa_webhook(self.webhook(order['provider_payment_id']))
        self.assertEqual(result['result']['reason'], 'payment_amount_mismatch')
        status = self.auth.access_status('operator-pay')
        self.assertFalse(status['allowed'])
        with self.auth.dbc() as connection:
            self.assertEqual(connection.execute('SELECT processing_status FROM payment_events').fetchone()[0], 'rejected')

    def test_metadata_mismatch_is_rejected_without_settlement(self):
        self.set_debt()
        with mock.patch.object(self.auth, 'yookassa_api', side_effect=self.fake_create_api):
            order = self.auth.create_yookassa_payment({'operator_id': 'operator-pay'})
        metadata = dict(self.created[order['provider_payment_id']]['metadata'])
        metadata['operator_id'] = 'another-operator'
        bad = self.succeeded(order['provider_payment_id'], metadata=metadata)
        with mock.patch.object(self.auth, 'yookassa_get_payment', return_value=bad):
            result = self.auth.process_yookassa_webhook(self.webhook(order['provider_payment_id']))
        self.assertEqual(result['result']['reason'], 'payment_metadata_mismatch')
        self.assertEqual(self.auth.access_status('operator-pay')['amount_due_minor'], 100)

    def test_payment_snapshot_preserves_usage_after_order_creation(self):
        self.set_debt(seconds=3600)
        with mock.patch.object(self.auth, 'yookassa_api', side_effect=self.fake_create_api):
            order = self.auth.create_yookassa_payment({'operator_id': 'operator-pay'})
        now = int(self.auth.time.time())
        with self.auth.dbc() as connection:
            connection.execute("UPDATE billing_accounts SET billable_seconds=5400,amount_due_minor=150,billing_status='blocked',blocked_at=? WHERE operator_id=?", (now, 'operator-pay'))
        with mock.patch.object(self.auth, 'yookassa_get_payment', return_value=self.succeeded(order['provider_payment_id'])):
            self.auth.process_yookassa_webhook(self.webhook(order['provider_payment_id']))
        status = self.auth.access_status('operator-pay')
        self.assertTrue(status['allowed'])
        self.assertEqual((status['billing_status'], status['amount_due_minor'], status['billable_seconds']), ('payment_due', 50, 1800))
        self.assertGreater(status['due_at'], now)

    def test_sync_reconciles_success_without_waiting_for_webhook(self):
        self.set_debt()
        with mock.patch.object(self.auth, 'yookassa_api', side_effect=self.fake_create_api):
            order = self.auth.create_yookassa_payment({'operator_id': 'operator-pay'})
        with mock.patch.object(self.auth, 'yookassa_get_payment', return_value=self.succeeded(order['provider_payment_id'])):
            result = self.auth.sync_yookassa_payment({'payment_order_id': order['payment_order_id']})
        self.assertEqual(result['reason'], 'payment_applied')
        self.assertEqual(result['payment']['status'], 'succeeded')
        self.assertTrue(result['payment']['access']['allowed'])

    def test_receipt_mode_requires_customer_email_and_configured_vat(self):
        self.set_debt()
        os.environ['YOOKASSA_RECEIPT_MODE'] = 'yookassa'
        os.environ['YOOKASSA_VAT_CODE'] = '11'
        with mock.patch.object(self.auth, 'yookassa_api', side_effect=self.fake_create_api):
            with self.assertRaises(self.auth.PaymentError) as ctx:
                self.auth.create_yookassa_payment({'operator_id': 'operator-pay'})
        self.assertEqual(ctx.exception.reason, 'receipt_email_required')


    def test_http_create_and_verified_webhook_flow(self):
        self.set_debt()
        server = self.auth.ThreadingHTTPServer(('127.0.0.1', 0), self.auth.Handler)
        server.signing_key = self.auth.ensure_key()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        def post(path, body):
            request = urllib.request.Request(
                base + path,
                data=json.dumps(body).encode(),
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        try:
            with mock.patch.object(self.auth, 'yookassa_api', side_effect=self.fake_create_api):
                code, created = post('/v1/payments/create', {'operator_id': 'operator-pay'})
            self.assertEqual(code, 200)
            self.assertTrue(created['ok'])
            provider_id = created['provider_payment_id']
            with mock.patch.object(self.auth, 'yookassa_get_payment', return_value=self.succeeded(provider_id)):
                code, webhook = post('/v1/webhooks/yookassa', self.webhook(provider_id))
            self.assertEqual(code, 200)
            self.assertTrue(webhook['ok'])
            status_url = base + '/v1/access/status?operator_id=operator-pay'
            with urllib.request.urlopen(status_url, timeout=5) as response:
                status = json.load(response)
            self.assertTrue(status['allowed'])
            self.assertEqual(status['amount_due_minor'], 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

if __name__ == '__main__':
    unittest.main()

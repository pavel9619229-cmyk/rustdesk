import base64
import importlib.util
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

SERVICE_FILE = Path(__file__).resolve().parents[1] / 'masha_auth.py'


def decode_base64url(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


class AuthorizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        os.environ['MASHA_AUTH_DIR'] = cls.temporary.name
        spec = importlib.util.spec_from_file_location('masha_auth_tested', SERVICE_FILE)
        cls.auth = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.auth)
        cls.auth.init_db()
        cls.key = cls.auth.ensure_key()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()
    def request(self, operator_id, **changes):
        body = {
            'operator_id': operator_id,
            'target_id': 'target-01',
            'session_id': 'session-01',
            'connection_type': 'remote',
            'client_version': '1.4.9',
        }
        body.update(changes)
        return self.auth.authorize(self.key, body)

    def start_lease(self, operator_id):
        self.auth.set_operator(operator_id, 'active')
        authorized = self.request(operator_id)
        self.assertTrue(authorized[0])
        started = self.auth.lease_start(self.key, {'ticket': authorized[2]})
        self.assertTrue(started[0])
        return started[2]

    def test_active_operator_receives_signed_ticket(self):
        self.auth.set_operator('active-operator', 'active')
        allowed, reason, ticket, expires_at = self.request('active-operator')
        self.assertTrue(allowed)
        self.assertEqual(reason, 'allowed')
        payload_text, signature_text = ticket.split('.')
        payload = decode_base64url(payload_text)
        signature = decode_base64url(signature_text)
        self.key.public_key().verify(signature, payload)
        claims = json.loads(payload)
        self.assertEqual(claims['operator_id'], 'active-operator')
        self.assertEqual(claims['target_id'], 'target-01')
        self.assertEqual(claims['session_id'], 'session-01')
        self.assertGreater(expires_at, int(time.time()))

    def test_session_id_is_required(self):
        self.auth.set_operator('session-test-operator', 'active')
        result = self.request('session-test-operator', session_id='')
        self.assertEqual(result[:2], (False, 'invalid_request'))

    def test_blocked_operator_is_denied(self):
        self.auth.set_operator('blocked-operator', 'blocked')
        result = self.request('blocked-operator')
        self.assertEqual(result[:2], (False, 'operator_blocked'))

    def test_expired_operator_is_denied(self):
        self.auth.set_operator(
            'expired-operator',
            'active',
            valid_until=int(time.time()) - 1,
        )
        result = self.request('expired-operator')
        self.assertEqual(result[:2], (False, 'operator_expired'))

    def test_unknown_operator_is_denied(self):
        result = self.request('unknown-operator')
        self.assertEqual(result[:2], (False, 'operator_unknown'))

    def test_direct_ip_nonce_is_required_and_signed(self):
        denied = self.request(
            'active-operator',
            connection_type='direct-ip',
        )
        self.assertEqual(denied[:2], (False, 'target_nonce_required'))
        allowed = self.request(
            'active-operator',
            connection_type='direct-ip',
            target_nonce='nonce-01',
        )
        payload = decode_base64url(allowed[2].split('.')[0])
        self.assertEqual(json.loads(payload)['target_nonce'], 'nonce-01')

    def test_lease_heartbeat_renews_active_session(self):
        lease = self.start_lease('lease-active-operator')
        result = self.auth.lease_action({
            'lease_id': lease['lease_id'],
            'lease_token': lease['lease_token'],
        })
        self.assertEqual(result[:2], (True, 'allowed'))

    def test_operator_revoke_stops_active_lease(self):
        lease = self.start_lease('lease-revoked-operator')
        self.auth.set_operator('lease-revoked-operator', 'blocked')
        result = self.auth.lease_action({
            'lease_id': lease['lease_id'],
            'lease_token': lease['lease_token'],
        })
        self.assertEqual(result[:2], (False, 'operator_blocked'))

    def test_heartbeat_loss_finishes_with_server_duration(self):
        lease = self.start_lease('lease-lost-operator')
        with self.auth.dbc() as connection:
            old = int(time.time()) - self.auth.LEASE_GRACE_SECONDS - 5
            connection.execute(
                'UPDATE leases SET started_at=?,last_heartbeat=? WHERE lease_id=?',
                (old - 10, old, lease['lease_id']),
            )
            self.auth.finish_stale(connection, int(time.time()))
            row = connection.execute(
                'SELECT finish_reason,duration_seconds FROM leases WHERE lease_id=?',
                (lease['lease_id'],),
            ).fetchone()
        self.assertEqual(row['finish_reason'], 'heartbeat_lost')
        self.assertEqual(row['duration_seconds'], 10 + self.auth.LEASE_GRACE_SECONDS)

    def test_finish_is_idempotent(self):
        lease = self.start_lease('lease-finish-operator')
        request = {
            'lease_id': lease['lease_id'],
            'lease_token': lease['lease_token'],
            'reason': 'normal_close',
        }
        first = self.auth.lease_action(request, finish=True)
        second = self.auth.lease_action(request, finish=True)
        self.assertEqual(first[:2], (True, 'finished'))
        self.assertEqual(second[:2], (True, 'finished'))
        self.assertEqual(first[2]['duration_seconds'], second[2]['duration_seconds'])

    def test_legacy_database_is_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'legacy.db'
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "CREATE TABLE operators("
                    "operator_id TEXT PRIMARY KEY,"
                    "access_status TEXT NOT NULL,"
                    "note TEXT NOT NULL DEFAULT '',"
                    "updated_at INTEGER NOT NULL)"
                )
                connection.commit()
            original_database = self.auth.DB
            self.auth.DB = database
            try:
                self.auth.init_db()
                with closing(sqlite3.connect(database)) as connection:
                    columns = {
                        row[1]
                        for row in connection.execute(
                            'PRAGMA table_info(operators)'
                        )
                    }
                self.assertIn('valid_until', columns)
            finally:
                self.auth.DB = original_database


if __name__ == '__main__':
    unittest.main()

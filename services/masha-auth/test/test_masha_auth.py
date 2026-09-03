import base64
import importlib.util
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from unittest import mock
from pathlib import Path

SERVICE_FILE = Path(os.environ.get(
    'MASHA_AUTH_SERVICE_FILE',
    Path(__file__).resolve().parents[1] / 'masha_auth.py',
))


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

    def start_time_credit_lease(self, operator_id, quota_seconds):
        self.auth.set_operator(operator_id, 'active')
        with self.auth.dbc() as connection:
            connection.execute("UPDATE access_grants SET status='revoked' WHERE operator_id=?", (operator_id,))
        grant_id = self.auth.set_grant(operator_id, 'ad_reward', 'time_credit', quota_seconds)
        authorized = self.request(operator_id, session_id='session-' + operator_id)
        self.assertTrue(authorized[0])
        started = self.auth.lease_start(self.key, {'ticket': authorized[2]})
        self.assertTrue(started[0])
        return grant_id, started[2]

    def start_postpaid_lease(self, operator_id, started_at=2_000_000_000):
        with mock.patch.object(self.auth.time, 'time', return_value=started_at):
            grant_id = self.auth.enable_postpaid(operator_id)
            authorized = self.request(
                operator_id,
                session_id='postpaid-session-' + operator_id,
            )
            self.assertTrue(authorized[0])
            started = self.auth.lease_start(self.key, {'ticket': authorized[2]})
            self.assertTrue(started[0])
        return grant_id, started[2]

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

    def test_each_grant_source_can_authorize(self):
        for source in ('payment', 'ad_reward', 'trial', 'promo', 'admin'):
            operator_id = 'grant-' + source
            self.auth.set_operator(operator_id, 'active')
            with self.auth.dbc() as connection:
                connection.execute("UPDATE access_grants SET status='revoked' WHERE operator_id=?", (operator_id,))
            grant_id = self.auth.set_grant(operator_id, source, expires_at=int(time.time()) + 60)
            result = self.request(operator_id, session_id='session-' + source)
            self.assertTrue(result[0], source)
            claims = json.loads(decode_base64url(result[2].split('.')[0]))
            self.assertEqual(claims['grant_id'], grant_id)
            self.assertEqual(claims['grant_source'], source)

    def test_repeated_init_does_not_shadow_new_grant(self):
        operator_id = 'grant-migration-regression'
        grant_id = self.auth.set_grant(operator_id, 'promo', expires_at=int(time.time()) + 60)
        self.auth.init_db()
        result = self.request(operator_id)
        claims = json.loads(decode_base64url(result[2].split('.')[0]))
        self.assertEqual(claims['grant_id'], grant_id)
        self.assertEqual(claims['grant_source'], 'promo')

    def test_repeated_source_event_does_not_duplicate_grant(self):
        operator_id = 'idempotent-source-event'
        first = self.auth.set_grant(operator_id, 'ad_reward', 'time_credit', 600, source_id='provider-event-001')
        repeated = self.auth.set_grant(operator_id, 'ad_reward', 'time_credit', 999, source_id='provider-event-001')
        self.assertEqual(first, repeated)
        with self.auth.dbc() as connection:
            rows = connection.execute("SELECT grant_id,quota_seconds FROM access_grants WHERE source_type='ad_reward' AND source_id='provider-event-001'").fetchall()
        self.assertEqual([(row['grant_id'], row['quota_seconds']) for row in rows], [(first, 600)])
        with self.assertRaises(ValueError):
            self.auth.set_grant('other-source-operator', 'ad_reward', 'time_credit', 600, source_id='provider-event-001')

    def test_configured_concurrent_session_limit(self):
        operator_id = 'concurrent-limit-operator'
        self.auth.set_operator(operator_id, 'active')
        self.auth.set_setting('max_concurrent_sessions', '2')
        leases = []
        try:
            tickets = [self.request(operator_id, session_id='concurrent-' + str(index))[2] for index in range(3)]
            first = self.auth.lease_start(self.key, {'ticket': tickets[0]})
            second = self.auth.lease_start(self.key, {'ticket': tickets[1]})
            third = self.auth.lease_start(self.key, {'ticket': tickets[2]})
            self.assertTrue(first[0])
            self.assertTrue(second[0])
            self.assertEqual(third[:2], (False, 'concurrent_session_limit'))
            leases.extend((first[2], second[2]))
            self.auth.lease_action({'lease_id': first[2]['lease_id'], 'lease_token': first[2]['lease_token']}, finish=True)
            accepted_after_finish = self.auth.lease_start(self.key, {'ticket': tickets[2]})
            self.assertTrue(accepted_after_finish[0])
            leases.append(accepted_after_finish[2])
        finally:
            self.auth.set_setting('max_concurrent_sessions', '1')
            for lease in leases:
                self.auth.lease_action({'lease_id': lease['lease_id'], 'lease_token': lease['lease_token']}, finish=True)

    def test_overdue_payment_does_not_block_alternative_grants(self):
        for source in ('ad_reward', 'trial', 'promo', 'admin'):
            operator_id = 'overdue-' + source
            self.auth.set_operator(operator_id, 'active')
            with self.auth.dbc() as connection:
                connection.execute("UPDATE access_grants SET status='revoked' WHERE operator_id=?", (operator_id,))
            self.auth.set_billing(operator_id, 'overdue')
            self.auth.set_grant(operator_id, source, expires_at=int(time.time()) + 60)
            self.assertTrue(self.request(operator_id, session_id='session-' + source)[0])

    def test_overdue_without_active_grant_is_denied(self):
        operator_id = 'overdue-no-grant'
        self.auth.set_operator(operator_id, 'active')
        with self.auth.dbc() as connection:
            connection.execute("UPDATE access_grants SET status='revoked' WHERE operator_id=?", (operator_id,))
        self.auth.set_billing(operator_id, 'overdue')
        self.assertEqual(self.request(operator_id)[:2], (False, 'payment_required'))

    def test_postpaid_one_hour_is_exactly_one_ruble_and_idempotent(self):
        operator_id = 'postpaid-one-hour'
        started_at = 2_000_000_000
        grant_id, lease = self.start_postpaid_lease(operator_id, started_at)
        request = {
            'lease_id': lease['lease_id'],
            'lease_token': lease['lease_token'],
            'reason': 'normal_close',
        }
        first_heartbeat = repeated_heartbeat = None
        for elapsed in range(10, 3600, 10):
            with mock.patch.object(
                self.auth.time,
                'time',
                return_value=started_at + elapsed,
            ):
                heartbeat = self.auth.lease_action(request)
                self.assertTrue(heartbeat[0], elapsed)
                if elapsed == 1800:
                    first_heartbeat = heartbeat
                    repeated_heartbeat = self.auth.lease_action(request)
        with mock.patch.object(self.auth.time, 'time', return_value=started_at + 3600):
            first_finish = self.auth.lease_action(request, finish=True)
            repeated_finish = self.auth.lease_action(request, finish=True)
        self.assertEqual(first_heartbeat[2]['duration_seconds'], 1800)
        self.assertEqual(repeated_heartbeat[2]['duration_seconds'], 1800)
        self.assertEqual(first_finish[2]['duration_seconds'], 3600)
        self.assertEqual(repeated_finish[2]['duration_seconds'], 3600)
        with self.auth.dbc() as connection:
            account = connection.execute(
                'SELECT billing_status,amount_due_minor,currency,billable_seconds '
                'FROM billing_accounts WHERE operator_id=?',
                (operator_id,),
            ).fetchone()
            consumption = connection.execute(
                'SELECT count(*) FROM grant_consumption WHERE grant_id=?',
                (grant_id,),
            ).fetchone()[0]
        self.assertEqual(tuple(account), ('payment_due', 100, 'RUB', 3600))
        self.assertEqual(consumption, 0)

    def test_postpaid_warning_and_blocking_are_server_driven(self):
        operator_id = 'postpaid-warning'
        now = 2_000_100_000
        with mock.patch.object(self.auth.time, 'time', return_value=now):
            self.auth.enable_postpaid(operator_id)
        with self.auth.dbc() as connection:
            connection.execute(
                "UPDATE billing_accounts SET billing_status='payment_due',"
                "amount_due_minor=100,billable_seconds=3600,due_at=?,grace_until=? "
                "WHERE operator_id=?",
                (now - 60, now + 600, operator_id),
            )
        warning = self.auth.access_status(operator_id, now)
        self.assertTrue(warning['allowed'])
        self.assertEqual(warning['billing_status'], 'overdue')
        self.assertTrue(warning['warning_10_minutes'])
        self.assertEqual(warning['seconds_until_block'], 600)
        blocked = self.auth.access_status(operator_id, now + 601)
        self.assertFalse(blocked['allowed'])
        self.assertEqual(blocked['reason'], 'payment_required')
        self.assertEqual(blocked['billing_status'], 'blocked')

    def test_blocked_postpaid_account_allows_alternative_grants(self):
        now = 2_000_200_000
        for source in ('ad_reward', 'promo', 'admin'):
            operator_id = 'postpaid-alternative-' + source
            with mock.patch.object(self.auth.time, 'time', return_value=now):
                self.auth.enable_postpaid(operator_id)
                grant_id = self.auth.set_grant(
                    operator_id,
                    source,
                    expires_at=now + 3600,
                )
            with self.auth.dbc() as connection:
                connection.execute(
                    "UPDATE billing_accounts SET billing_status='blocked',"
                    "amount_due_minor=100,blocked_at=? WHERE operator_id=?",
                    (now, operator_id),
                )
            status = self.auth.access_status(operator_id, now)
            self.assertTrue(status['allowed'], source)
            self.assertEqual(status['grant_id'], grant_id)
            self.assertEqual(status['grant_source'], source)
            self.assertEqual(status['billing_status'], 'blocked')

    def test_settle_resets_postpaid_debt(self):
        operator_id = 'postpaid-settle'
        now = 2_000_300_000
        with mock.patch.object(self.auth.time, 'time', return_value=now):
            self.auth.enable_postpaid(operator_id)
        with self.auth.dbc() as connection:
            connection.execute(
                "UPDATE billing_accounts SET billing_status='blocked',"
                "amount_due_minor=250,billable_seconds=9000,due_at=?,"
                "grace_until=?,blocked_at=? WHERE operator_id=?",
                (now - 1000, now - 1, now, operator_id),
            )
        self.assertTrue(self.auth.settle_postpaid(operator_id))
        status = self.auth.access_status(operator_id, now)
        self.assertTrue(status['allowed'])
        self.assertEqual(status['billing_status'], 'current')
        self.assertEqual(status['amount_due_minor'], 0)

    def test_postpaid_heartbeat_loss_uses_server_duration(self):
        operator_id = 'postpaid-heartbeat-loss'
        now = 2_000_400_000
        _, lease = self.start_postpaid_lease(operator_id, now - 70)
        with self.auth.dbc() as connection:
            connection.execute(
                'UPDATE leases SET started_at=?,last_heartbeat=? WHERE lease_id=?',
                (now - 70, now - self.auth.LEASE_GRACE_SECONDS, lease['lease_id']),
            )
            connection.execute(
                'UPDATE usage_sessions SET started_at=?,last_heartbeat_at=? '
                'WHERE lease_id=?',
                (now - 70, now - self.auth.LEASE_GRACE_SECONDS, lease['lease_id']),
            )
            self.auth.finish_stale(connection, now)
            usage = connection.execute(
                'SELECT duration_seconds,close_reason FROM usage_sessions '
                'WHERE lease_id=?',
                (lease['lease_id'],),
            ).fetchone()
            account = connection.execute(
                'SELECT billable_seconds,amount_due_minor FROM billing_accounts '
                'WHERE operator_id=?',
                (operator_id,),
            ).fetchone()
        self.assertEqual(tuple(usage), (70, 'heartbeat_lost'))
        self.assertEqual(tuple(account), (70, 1))

    def test_access_status_http_endpoint(self):
        operator_id = 'postpaid-http-status'
        self.auth.enable_postpaid(operator_id)
        server = self.auth.ThreadingHTTPServer(
            ('127.0.0.1', 0),
            self.auth.Handler,
        )
        server.signing_key = self.key
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = (
                f'http://127.0.0.1:{server.server_port}/v1/access/status'
                f'?operator_id={operator_id}'
            )
            with urllib.request.urlopen(url, timeout=5) as response:
                status = json.load(response)
            self.assertEqual(response.status, 200)
            self.assertTrue(status['allowed'])
            self.assertEqual(status['policy_mode'], 'postpaid')
            self.assertEqual(status['policy_id'], 'postpaid-default')
            self.assertEqual(status['rate_minor_per_hour'], 100)
            self.assertEqual(status['payment_due_seconds'], 86400)
            self.assertEqual(status['grace_seconds'], 3600)
            self.assertEqual(status['warning_seconds'], 600)
            self.assertEqual(status['billable_seconds'], 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_download_endpoint_serves_only_the_release_file(self):
        filename = next(iter(self.auth.DOWNLOAD_FILES))
        download_root = Path(self.temporary.name) / 'downloads'
        download_root.mkdir(exist_ok=True)
        payload = b'masha-stage-2.0-test-archive'
        (download_root / filename).write_bytes(payload)
        previous_root = self.auth.DOWNLOAD_ROOT
        self.auth.DOWNLOAD_ROOT = download_root
        server = self.auth.ThreadingHTTPServer(
            ('127.0.0.1', 0),
            self.auth.Handler,
        )
        server.signing_key = self.key
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/downloads/{filename}'
            with urllib.request.urlopen(url, timeout=5) as response:
                self.assertEqual(response.read(), payload)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers['Content-Type'], 'application/zip')
                self.assertEqual(response.headers['Content-Length'], str(len(payload)))
            request = urllib.request.Request(url, method='HEAD')
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b'')
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                urllib.request.urlopen(
                    f'http://127.0.0.1:{server.server_port}/downloads/unknown.zip',
                    timeout=5,
                )
            self.assertEqual(rejected.exception.code, 404)
            rejected.exception.close()
        finally:
            self.auth.DOWNLOAD_ROOT = previous_root
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_expired_and_revoked_grants_are_denied(self):
        operator_id = 'inactive-grants'
        self.auth.set_operator(operator_id, 'active')
        with self.auth.dbc() as connection:
            connection.execute("UPDATE access_grants SET status='revoked' WHERE operator_id=?", (operator_id,))
        expired = self.auth.set_grant(operator_id, 'promo', expires_at=int(time.time()) - 1)
        revoked = self.auth.set_grant(operator_id, 'admin')
        with self.auth.dbc() as connection:
            connection.execute("UPDATE access_grants SET status='revoked' WHERE grant_id=?", (revoked,))
        self.assertEqual(self.request(operator_id)[:2], (False, 'no_active_grant'))
        with self.auth.dbc() as connection:
            self.assertEqual(connection.execute('SELECT status FROM access_grants WHERE grant_id=?', (expired,)).fetchone()['status'], 'expired')

    def test_usage_accounting_is_incremental_and_idempotent(self):
        grant_id, lease = self.start_time_credit_lease('usage-idempotent', 100)
        request = {'lease_id': lease['lease_id'], 'lease_token': lease['lease_token']}
        with mock.patch.object(self.auth.time, 'time', return_value=lease['started_at'] + 20):
            first_heartbeat = self.auth.lease_action(request)
            repeated_heartbeat = self.auth.lease_action(request)
        self.assertEqual(first_heartbeat[2]['duration_seconds'], 20)
        self.assertEqual(repeated_heartbeat[2]['duration_seconds'], 20)
        with mock.patch.object(self.auth.time, 'time', return_value=lease['started_at'] + 40):
            first_finish = self.auth.lease_action({**request, 'reason': 'normal_close'}, finish=True)
            repeated_finish = self.auth.lease_action({**request, 'reason': 'normal_close'}, finish=True)
        self.assertEqual(first_finish[2]['duration_seconds'], 40)
        self.assertEqual(repeated_finish[2]['duration_seconds'], 40)
        with self.auth.dbc() as connection:
            grant = connection.execute('SELECT quota_seconds FROM access_grants WHERE grant_id=?', (grant_id,)).fetchone()
            usage = connection.execute('SELECT duration_seconds,accounted_seconds FROM usage_sessions WHERE lease_id=?', (lease['lease_id'],)).fetchone()
            consumption = connection.execute('SELECT seconds FROM grant_consumption WHERE lease_id=?', (lease['lease_id'],)).fetchall()
        self.assertEqual(grant['quota_seconds'], 60)
        self.assertEqual(tuple(usage), (40, 40))
        self.assertEqual([row['seconds'] for row in consumption], [40])

    def test_repeated_start_and_session_id_do_not_duplicate_usage(self):
        operator_id = 'usage-start-idempotent'
        self.auth.set_operator(operator_id, 'active')
        first_ticket = self.request(operator_id, session_id='same-session')[2]
        second_ticket = self.request(operator_id, session_id='same-session')[2]
        first = self.auth.lease_start(self.key, {'ticket': first_ticket})
        replay = self.auth.lease_start(self.key, {'ticket': first_ticket})
        duplicate_session = self.auth.lease_start(self.key, {'ticket': second_ticket})
        self.assertTrue(first[0])
        self.assertEqual(replay[:2], (False, 'ticket_replayed'))
        self.assertEqual(duplicate_session[:2], (False, 'session_replayed'))
        with self.auth.dbc() as connection:
            count = connection.execute("SELECT count(*) FROM usage_sessions WHERE operator_id=? AND session_id='same-session'", (operator_id,)).fetchone()[0]
        self.assertEqual(count, 1)

    def test_time_credit_exhaustion_closes_and_caps_session(self):
        grant_id, lease = self.start_time_credit_lease('usage-exhausted', 15)
        request = {'lease_id': lease['lease_id'], 'lease_token': lease['lease_token']}
        with mock.patch.object(self.auth.time, 'time', return_value=lease['started_at'] + 20):
            heartbeat = self.auth.lease_action(request)
            repeated_finish = self.auth.lease_action(request, finish=True)
        self.assertEqual(heartbeat[:2], (False, 'quota_exhausted'))
        self.assertEqual(heartbeat[2]['duration_seconds'], 15)
        self.assertEqual(repeated_finish[2]['duration_seconds'], 15)
        with self.auth.dbc() as connection:
            grant = connection.execute('SELECT quota_seconds,status FROM access_grants WHERE grant_id=?', (grant_id,)).fetchone()
            consumption = connection.execute('SELECT seconds FROM grant_consumption WHERE lease_id=?', (lease['lease_id'],)).fetchone()
        self.assertEqual(tuple(grant), (0, 'consumed'))
        self.assertEqual(consumption['seconds'], 15)

    def test_grant_revoke_stops_active_lease(self):
        lease = self.start_lease('lease-grant-revoke')
        with self.auth.dbc() as connection:
            connection.execute("UPDATE access_grants SET status='revoked' WHERE operator_id=?", ('lease-grant-revoke',))
        result = self.auth.lease_action({
            'lease_id': lease['lease_id'],
            'lease_token': lease['lease_token'],
        })
        self.assertEqual(result[:2], (False, 'no_active_grant'))

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
            connection.execute(
                'UPDATE usage_sessions SET started_at=?,last_heartbeat_at=? WHERE lease_id=?',
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

    def test_stage_one_access_grants_are_preserved_by_postpaid_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'stage-one.db'
            now = int(time.time())
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "CREATE TABLE access_grants("
                    "grant_id TEXT PRIMARY KEY,operator_id TEXT NOT NULL,"
                    "source_type TEXT NOT NULL CHECK(source_type IN "
                    "('payment','ad_reward','trial','promo','admin')),"
                    "grant_kind TEXT NOT NULL CHECK(grant_kind IN "
                    "('time_credit','unlimited_period')),quota_seconds INTEGER,"
                    "starts_at INTEGER NOT NULL,expires_at INTEGER,status TEXT NOT NULL,"
                    "priority INTEGER NOT NULL DEFAULT 100,source_id TEXT,"
                    "created_at INTEGER NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO access_grants VALUES("
                    "'old-grant','old-operator','promo','unlimited_period',"
                    "NULL,?,NULL,'active',100,'old-event',?)",
                    (now, now),
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
                            'PRAGMA table_info(access_grants)'
                        )
                    }
                    preserved = connection.execute(
                        'SELECT source_type,grant_kind,status FROM access_grants '
                        "WHERE grant_id='old-grant'"
                    ).fetchone()
                    connection.execute(
                        "INSERT INTO access_grants("
                        "grant_id,operator_id,source_type,grant_kind,starts_at,"
                        "status,priority,source_id,created_at) VALUES("
                        "'postpaid-test','postpaid-operator','postpaid_account',"
                        "'postpaid_account',?,'active',500,'postpaid-event',?)",
                        (now, now),
                    )
                self.assertIn('metadata_json', columns)
                self.assertEqual(preserved, ('promo', 'unlimited_period', 'active'))
            finally:
                self.auth.DB = original_database


    def test_tls_listener_defers_handshake_and_limits_client_timeout(self):
        self.assertEqual(self.auth.Handler.timeout, 10)
        fake_server = mock.Mock()
        fake_server.socket = object()
        original_socket = fake_server.socket
        fake_server.serve_forever.side_effect = RuntimeError('stop-test-server')
        fake_context = mock.Mock()
        old_env = dict(os.environ)
        try:
            os.environ['MASHA_AUTH_TLS_CERT'] = 'unit-cert.pem'
            os.environ['MASHA_AUTH_TLS_KEY'] = 'unit-key.pem'
            with mock.patch.object(self.auth, 'init_db'), \
                 mock.patch.object(self.auth, 'ensure_key', return_value=self.key), \
                 mock.patch.object(self.auth, 'ThreadingHTTPServer', return_value=fake_server), \
                 mock.patch.object(self.auth.ssl, 'SSLContext', return_value=fake_context):
                with self.assertRaisesRegex(RuntimeError, 'stop-test-server'):
                    self.auth.serve()
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        fake_context.load_cert_chain.assert_called_once_with('unit-cert.pem', 'unit-key.pem')
        fake_context.wrap_socket.assert_called_once_with(
            original_socket, server_side=True, do_handshake_on_connect=False)


if __name__ == '__main__':
    unittest.main()

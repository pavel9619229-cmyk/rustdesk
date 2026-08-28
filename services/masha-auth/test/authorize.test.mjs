import assert from 'node:assert/strict';
import { generateKeyPairSync, verify } from 'node:crypto';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, test } from 'node:test';

import { createAuthorizeService } from '../server.mjs';

const NOW = 2_000_000_000;
let baseUrl;
let operatorsFile;
let publicKey;
let service;
let tempDirectory;

function operatorsDocument() {
  return {
    operators: {
      active_operator: {
        status: 'active',
        valid_until: new Date((NOW + 3600) * 1000).toISOString(),
      },
      blocked_operator: { status: 'blocked' },
      expired_operator: { status: 'expired' },
      elapsed_operator: {
        status: 'active',
        valid_until: new Date((NOW - 1) * 1000).toISOString(),
      },
    },
  };
}
async function authorize(operatorId, extra = {}) {
  return fetch(`${baseUrl}/v1/session/authorize`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      operator_id: operatorId,
      target_id: 'target_01',
      connection_type: 'remote',
      client_version: '1.4.9',
      ...extra,
    }),
  });
}

before(async () => {
  tempDirectory = await mkdtemp(join(tmpdir(), 'masha-auth-test-'));
  const privateKeyFile = join(tempDirectory, 'private-key.pem');
  operatorsFile = join(tempDirectory, 'operators.json');
  const pair = generateKeyPairSync('ed25519');
  publicKey = pair.publicKey;
  await writeFile(
    privateKeyFile,
    pair.privateKey.export({ format: 'pem', type: 'pkcs8' }),
    { mode: 0o600 },
  );
  await writeFile(
    operatorsFile,
    JSON.stringify(operatorsDocument()),
  );
  service = createAuthorizeService({
    nowSeconds: () => NOW,
    operatorsFile,
    privateKeyFile,
    ticketTtlSeconds: 120,
  });
  await new Promise((resolve, reject) => {
    service.server.once('error', reject);
    service.server.listen(0, '127.0.0.1', resolve);
  });
  const address = service.server.address();
  baseUrl = `http://127.0.0.1:${address.port}`;
});

after(async () => {
  if (service) {
    await new Promise((resolve) => service.server.close(resolve));
  }
  if (tempDirectory) {
    await rm(tempDirectory, { recursive: true, force: true });
  }
});

test('active operator receives a valid signed ticket', async () => {
  const response = await authorize('active_operator');
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.allowed, true);
  assert.equal(typeof body.ticket, 'string');
  const [payloadText, signatureText] = body.ticket.split('.');
  const payload = Buffer.from(payloadText, 'base64url');
  const signature = Buffer.from(signatureText, 'base64url');
  assert.equal(verify(null, payload, publicKey, signature), true);

  const claims = JSON.parse(payload.toString('utf8'));
  assert.equal(claims.v, 1);
  assert.equal(claims.iss, 'masha-auth');
  assert.equal(claims.operator_id, 'active_operator');
  assert.equal(claims.target_id, 'target_01');
  assert.equal(claims.connection_type, 'remote');
  assert.equal(claims.client_version, '1.4.9');
  assert.equal(claims.iat, NOW);
  assert.equal(claims.exp, NOW + 120);
  assert.match(claims.jti, /^[0-9a-f-]{36}$/);
  assert.equal(body.expires_at, new Date((NOW + 120) * 1000).toISOString());
});

for (const [operatorId, reason] of [
  ['blocked_operator', 'operator_blocked'],
  ['expired_operator', 'operator_expired'],
  ['elapsed_operator', 'operator_expired'],
]) {
  test(`${operatorId} is denied with ${reason}`, async () => {
    const response = await authorize(operatorId);
    assert.equal(response.status, 403);
    assert.deepEqual(await response.json(), {
      allowed: false,
      reason,
    });
  });
}

test('unknown operator is denied', async () => {
  const response = await authorize('missing_operator');
  assert.equal(response.status, 403);
  assert.deepEqual(await response.json(), {
    allowed: false,
    reason: 'operator_unknown',
  });
});

test('Direct IP requires a target nonce and binds it into the ticket', async () => {
  const denied = await authorize('active_operator', {
    connection_type: 'direct-ip',
  });
  assert.equal(denied.status, 400);
  assert.equal((await denied.json()).reason, 'target_nonce_required');

  const allowed = await authorize('active_operator', {
    connection_type: 'direct-ip',
    target_nonce: 'nonce_01',
  });
  const body = await allowed.json();
  const claims = JSON.parse(
    Buffer.from(body.ticket.split('.')[0], 'base64url').toString('utf8'),
  );
  assert.equal(claims.target_nonce, 'nonce_01');
});

test('operator status changes apply without restarting the service', async () => {
  const document = operatorsDocument();
  document.operators.active_operator.status = 'blocked';
  await writeFile(operatorsFile, JSON.stringify(document));

  const denied = await authorize('active_operator');
  assert.equal(denied.status, 403);
  assert.equal((await denied.json()).reason, 'operator_blocked');

  await writeFile(
    operatorsFile,
    JSON.stringify(operatorsDocument()),
  );
  const allowed = await authorize('active_operator');
  assert.equal(allowed.status, 200);
});

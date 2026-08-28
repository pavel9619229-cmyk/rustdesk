import { createServer } from 'node:http';
import {
  createPrivateKey,
  createPublicKey,
  randomUUID,
  sign,
} from 'node:crypto';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const MAX_BODY_BYTES = 16 * 1024;
const IDENTIFIER_PATTERN = /^[A-Za-z0-9._:@-]+$/;
const CONNECTION_TYPE_PATTERN = /^[a-z0-9_-]+$/;

function base64Url(value) {
  return Buffer.from(value).toString('base64url');
}

function sendJson(response, statusCode, body) {
  const data = Buffer.from(JSON.stringify(body));
  response.writeHead(statusCode, {
    'Cache-Control': 'no-store',
    'Content-Length': data.length,
    'Content-Type': 'application/json; charset=utf-8',
    'X-Content-Type-Options': 'nosniff',
  });
  response.end(data);
}

function deny(response, statusCode, reason) {
  sendJson(response, statusCode, { allowed: false, reason });
}
async function readJson(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) {
      const error = new Error('request body is too large');
      error.code = 'body_too_large';
      throw error;
    }
    chunks.push(chunk);
  }
  if (size === 0) {
    const error = new Error('request body is empty');
    error.code = 'invalid_json';
    throw error;
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    const error = new Error('request body is not valid JSON');
    error.code = 'invalid_json';
    throw error;
  }
}

function requiredString(value, maxLength, pattern) {
  return typeof value === 'string'
    && value.length > 0
    && value.length <= maxLength
    && pattern.test(value);
}
function loadOperators(filePath) {
  const document = JSON.parse(readFileSync(filePath, 'utf8'));
  if (!document || typeof document.operators !== 'object') {
    throw new Error('operators file must contain an operators object');
  }
  return document.operators;
}

function operatorDenialReason(operator, nowSeconds) {
  if (!operator) {
    return 'operator_unknown';
  }
  if (operator.status === 'blocked') {
    return 'operator_blocked';
  }
  if (operator.status === 'expired') {
    return 'operator_expired';
  }
  if (operator.status !== 'active') {
    return 'operator_inactive';
  }
  if (operator.valid_until !== undefined && operator.valid_until !== null) {
    const validUntil = Date.parse(operator.valid_until);
    if (!Number.isFinite(validUntil) || validUntil <= nowSeconds * 1000) {
      return 'operator_expired';
    }
  }
  return null;
}

function validateAuthorizeRequest(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return 'invalid_request';
  }
  if (!requiredString(body.operator_id, 128, IDENTIFIER_PATTERN)
      || !requiredString(body.target_id, 128, IDENTIFIER_PATTERN)
      || !requiredString(body.connection_type, 64, CONNECTION_TYPE_PATTERN)
      || !requiredString(body.client_version, 64, /^[A-Za-z0-9._+()-]+$/)) {
    return 'invalid_request';
  }
  if (body.target_nonce !== undefined
      && !requiredString(body.target_nonce, 256, IDENTIFIER_PATTERN)) {
    return 'invalid_request';
  }
  if (body.connection_type === 'direct-ip' && !body.target_nonce) {
    return 'target_nonce_required';
  }
  return null;
}

function createTicket(body, options) {
  const iat = options.nowSeconds();
  const claims = {
    v: 1,
    iss: options.issuer,
    operator_id: body.operator_id,
    target_id: body.target_id,
    connection_type: body.connection_type,
    client_version: body.client_version,
    iat,
    exp: iat + options.ticketTtlSeconds,
    jti: randomUUID(),
  };
  if (body.target_nonce) {
    claims.target_nonce = body.target_nonce;
  }
  const payload = Buffer.from(JSON.stringify(claims));
  const signature = sign(null, payload, options.privateKey);
  return {
    claims,
    ticket: `${base64Url(payload)}.${base64Url(signature)}`,
  };
}

export function createAuthorizeService(options) {
  if (!options?.privateKeyFile || !options?.operatorsFile) {
    throw new Error('privateKeyFile and operatorsFile are required');
  }
  const privateKey = createPrivateKey(
    readFileSync(options.privateKeyFile, 'utf8'),
  );
  const publicJwk = createPublicKey(privateKey).export({ format: 'jwk' });
  if (!publicJwk.x) {
    throw new Error('the configured key is not Ed25519');
  }
  const runtime = {
    issuer: options.issuer ?? 'masha-auth',
    nowSeconds: options.nowSeconds ?? (() => Math.floor(Date.now() / 1000)),
    operatorsFile: options.operatorsFile,
    privateKey,
    ticketTtlSeconds: options.ticketTtlSeconds ?? 120,
  };
  if (!Number.isInteger(runtime.ticketTtlSeconds)
      || runtime.ticketTtlSeconds < 30
      || runtime.ticketTtlSeconds > 600) {
    throw new Error('ticketTtlSeconds must be an integer from 30 to 600');
  }
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url ?? '/', 'http://localhost');
      if (request.method === 'GET' && url.pathname === '/healthz') {
        sendJson(response, 200, { status: 'ok' });
        return;
      }
      if (request.method !== 'POST'
          || url.pathname !== '/v1/session/authorize') {
        deny(response, 404, 'not_found');
        return;
      }

      let body;
      try {
        body = await readJson(request);
      } catch (error) {
        deny(response, error.code === 'body_too_large' ? 413 : 400,
          error.code ?? 'invalid_json');
        return;
      }

      const invalidReason = validateAuthorizeRequest(body);
      if (invalidReason) {
        deny(response, 400, invalidReason);
        return;
      }
      const operators = loadOperators(runtime.operatorsFile);
      const denialReason = operatorDenialReason(
        operators[body.operator_id],
        runtime.nowSeconds(),
      );
      if (denialReason) {
        deny(response, 403, denialReason);
        return;
      }

      const { claims, ticket } = createTicket(body, runtime);
      sendJson(response, 200, {
        allowed: true,
        ticket,
        expires_at: new Date(claims.exp * 1000).toISOString(),
      });
    } catch (error) {
      console.error('authorize request failed:', error.message);
      deny(response, 500, 'internal_error');
    }
  });

  return {
    publicKeyBase64: Buffer.from(publicJwk.x, 'base64url').toString('base64'),
    server,
  };
}
function integerFromEnvironment(value, fallback) {
  if (value === undefined || value === '') {
    return fallback;
  }
  const parsed = Number(value);
  if (!Number.isInteger(parsed)) {
    throw new Error('numeric environment value is not an integer');
  }
  return parsed;
}

export async function startFromEnvironment(environment = process.env) {
  const host = environment.MASHA_AUTH_HOST ?? '127.0.0.1';
  const port = integerFromEnvironment(environment.MASHA_AUTH_PORT, 8443);
  const service = createAuthorizeService({
    issuer: environment.MASHA_AUTH_ISSUER ?? 'masha-auth',
    operatorsFile: environment.MASHA_AUTH_OPERATORS_FILE,
    privateKeyFile: environment.MASHA_AUTH_PRIVATE_KEY_FILE,
    ticketTtlSeconds: integerFromEnvironment(
      environment.MASHA_AUTH_TICKET_TTL_SECONDS,
      120,
    ),
  });
  await new Promise((resolve, reject) => {
    service.server.once('error', reject);
    service.server.listen(port, host, resolve);
  });
  console.log(`masha-auth listening on ${host}:${port}`);
  console.log(`Ed25519 public key: ${service.publicKeyBase64}`);
  return service;
}
if (process.argv[1]
    && import.meta.url === pathToFileURL(process.argv[1]).href) {
  startFromEnvironment().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}

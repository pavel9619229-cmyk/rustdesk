import {
  createPublicKey,
  generateKeyPairSync,
} from 'node:crypto';
import {
  existsSync,
  mkdirSync,
  writeFileSync,
} from 'node:fs';
import { resolve } from 'node:path';

const outputDirectory = process.argv[2];
if (!outputDirectory) {
  console.error('Usage: node generate-keys.mjs <output-directory>');
  process.exit(2);
}

const directory = resolve(outputDirectory);
const privateKeyFile = resolve(directory, 'private-key.pem');
const publicKeyFile = resolve(directory, 'public-key-base64.txt');
if (existsSync(privateKeyFile) || existsSync(publicKeyFile)) {
  console.error('Refusing to overwrite an existing key file.');
  process.exit(1);
}
mkdirSync(directory, { recursive: true, mode: 0o700 });
const pair = generateKeyPairSync('ed25519');
const privateKey = pair.privateKey.export({
  format: 'pem',
  type: 'pkcs8',
});
const publicJwk = createPublicKey(pair.privateKey).export({ format: 'jwk' });
const publicKeyBase64 = Buffer.from(
  publicJwk.x,
  'base64url',
).toString('base64');

writeFileSync(privateKeyFile, privateKey, { mode: 0o600 });
writeFileSync(publicKeyFile, `${publicKeyBase64}\n`, { mode: 0o644 });

console.log(`Private key written to ${privateKeyFile}`);
console.log(`Public key written to ${publicKeyFile}`);
console.log(`Ed25519 public key: ${publicKeyBase64}`);

# Stage 1.3 — all connection paths

Status: completed on 2026-08-28.

## Implemented

- The operator requests a fresh signed authorization ticket after receiving the target's per-connection challenge.
- The request binds operator ID, target ID, session ID, actual connection type, client version, and target nonce.
- Login is not sent when authorize is unavailable, denied, malformed, or returns an empty ticket.
- The ticket is carried in a versioned binary login envelope; the original password bytes are restored only after ticket verification.
- The receiver applies one shared gate before normal login processing.
- The same gate covers normal LoginRequest and SwitchSidesResponse login.

## Covered paths

- Direct TCP connection.
- Manual Direct IP server.
- Rendezvous / hole-punch direct connection.
- KCP / UDP direct connection.
- Relay connection.
- Unknown or unclassified entry path is denied.

## Receiver checks

The gate verifies Ed25519 signature, iat, exp, operator/target/session bindings, actual Direct/Relay path, target nonce, and one-time jti replay protection. Any failure returns a generic authorization denial and stops login processing.

## Verification

Command: `cargo test --release --lib masha_ticket::tests`

Result: 12 passed, 0 failed. The release test build compiled the modified client, receiver, rendezvous, server entry points, and ticket module.

Stage 1.4 is not included in this checkpoint.

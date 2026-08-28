use hbb_common::{
    bail,
    base64::{
        engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD},
        Engine as _,
    },
    sodiumoxide::{self, crypto::sign},
    ResultType,
};
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    fmt,
    sync::Mutex,
    time::{SystemTime, UNIX_EPOCH},
};

pub const MASHA_AUTH_PUBLIC_KEY_B64: &str = "ScrTUazLLtnsMXrbZUPcXYcyNWx7JgXS6quKrIpHGy4=";
pub const MASHA_AUTH_URL: &str = "https://77.222.38.70:8443/v1/session/authorize";
const TICKET_VERSION: u8 = 1;
const TICKET_ISSUER: &str = "masha-auth";
const MAX_CLOCK_SKEW_SECONDS: i64 = 30;
const MAX_TICKET_TTL_SECONDS: i64 = 600;
const LOGIN_ENVELOPE_MAGIC: &[u8; 8] = b"MSHTKT01";

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum ConnectionPath {
    #[default]
    Unknown,
    Direct,
    Relay,
}

impl ConnectionPath {
    pub fn ticket_value(self) -> Option<&'static str> {
        match self {
            Self::Unknown => None,
            Self::Direct => Some("direct-ip"),
            Self::Relay => Some("relay"),
        }
    }
}

pub fn wrap_login_password(password: Vec<u8>, ticket: &str) -> Vec<u8> {
    let mut envelope = Vec::with_capacity(16 + ticket.len() + password.len());
    envelope.extend_from_slice(LOGIN_ENVELOPE_MAGIC);
    envelope.extend_from_slice(&(ticket.len() as u64).to_be_bytes());
    envelope.extend_from_slice(ticket.as_bytes());
    envelope.extend_from_slice(&password);
    envelope
}

pub fn unwrap_login_password(envelope: &[u8]) -> Result<(String, Vec<u8>), TicketError> {
    if envelope.len() < 16 || &envelope[..8] != LOGIN_ENVELOPE_MAGIC {
        return Err(TicketError::InvalidFormat);
    }
    let ticket_len = usize::try_from(u64::from_be_bytes(
        envelope[8..16]
            .try_into()
            .map_err(|_| TicketError::InvalidFormat)?,
    ))
    .map_err(|_| TicketError::InvalidFormat)?;
    let ticket_end = 16usize
        .checked_add(ticket_len)
        .filter(|end| *end <= envelope.len())
        .ok_or(TicketError::InvalidFormat)?;
    let ticket = std::str::from_utf8(&envelope[16..ticket_end])
        .map_err(|_| TicketError::InvalidFormat)?
        .to_owned();
    if ticket.is_empty() {
        return Err(TicketError::InvalidFormat);
    }
    Ok((ticket, envelope[ticket_end..].to_vec()))
}

#[derive(Debug, Serialize)]
pub struct AuthorizeRequest {
    pub operator_id: String,
    pub target_id: String,
    pub session_id: String,
    pub connection_type: String,
    pub client_version: String,
    pub target_nonce: String,
}

#[derive(Debug, Deserialize)]
struct AuthorizeResponse {
    allowed: bool,
    #[serde(default)]
    ticket: String,
    #[serde(default)]
    reason: String,
}

#[derive(Clone, Copy, Debug)]
pub struct TicketBindings<'a> {
    pub operator_id: &'a str,
    pub target_id: &'a str,
    pub session_id: &'a str,
    pub connection_type: &'a str,
    pub target_nonce: Option<&'a str>,
}
#[derive(Debug, PartialEq, Eq)]
pub struct VerifiedTicket {
    pub operator_id: String,
    pub target_id: String,
    pub session_id: String,
    pub connection_type: String,
    pub target_nonce: Option<String>,
    pub jti: String,
    pub issued_at: i64,
    pub expires_at: i64,
}

#[derive(Debug, PartialEq, Eq)]
pub enum TicketError {
    CryptoUnavailable,
    InvalidFormat,
    InvalidPayloadEncoding,
    InvalidSignatureEncoding,
    InvalidPublicKey,
    InvalidSignature,
    InvalidClaims,
    UnsupportedVersion,
    InvalidIssuer,
    IssuedInFuture,
    Expired,
    ClockUnavailable,
    UnknownConnectionPath,
    BindingMismatch(&'static str),
    Replay,
    ReplayCacheUnavailable,
}
impl fmt::Display for TicketError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::CryptoUnavailable => f.write_str("ticket crypto unavailable"),
            Self::InvalidFormat => f.write_str("invalid ticket format"),
            Self::InvalidPayloadEncoding => f.write_str("invalid ticket payload encoding"),
            Self::InvalidSignatureEncoding => f.write_str("invalid ticket signature encoding"),
            Self::InvalidPublicKey => f.write_str("invalid ticket public key"),
            Self::InvalidSignature => f.write_str("invalid ticket signature"),
            Self::InvalidClaims => f.write_str("invalid ticket claims"),
            Self::UnsupportedVersion => f.write_str("unsupported ticket version"),
            Self::InvalidIssuer => f.write_str("invalid ticket issuer"),
            Self::IssuedInFuture => f.write_str("ticket issued in the future"),
            Self::Expired => f.write_str("ticket expired"),
            Self::ClockUnavailable => f.write_str("system clock unavailable"),
            Self::UnknownConnectionPath => f.write_str("unknown connection path"),
            Self::BindingMismatch(field) => write!(f, "ticket binding mismatch: {field}"),
            Self::Replay => f.write_str("ticket replay"),
            Self::ReplayCacheUnavailable => f.write_str("ticket replay cache unavailable"),
        }
    }
}

impl std::error::Error for TicketError {}
#[derive(Debug, Deserialize)]
struct TicketClaims {
    v: u8,
    iss: String,
    operator_id: String,
    target_id: String,
    session_id: String,
    connection_type: String,
    client_version: String,
    iat: i64,
    exp: i64,
    jti: String,
    #[serde(default)]
    target_nonce: Option<String>,
}

#[derive(Default)]
pub struct ReplayCache {
    seen: Mutex<HashMap<String, i64>>,
}

impl ReplayCache {
    fn consume(&self, jti: &str, expires_at: i64, now: i64) -> Result<(), TicketError> {
        let mut seen = self
            .seen
            .lock()
            .map_err(|_| TicketError::ReplayCacheUnavailable)?;
        seen.retain(|_, stored_expiry| *stored_expiry > now);
        if seen.contains_key(jti) {
            return Err(TicketError::Replay);
        }
        seen.insert(jti.to_owned(), expires_at);
        Ok(())
    }
}

pub async fn request_authorize_ticket(request: &AuthorizeRequest) -> ResultType<String> {
    if request.operator_id.is_empty()
        || request.target_id.is_empty()
        || request.session_id.is_empty()
        || request.connection_type.is_empty()
        || request.client_version.is_empty()
        || request.target_nonce.is_empty()
    {
        bail!("incomplete Masha authorization binding");
    }

    let client = crate::hbbs_http::create_http_client_async_with_url(MASHA_AUTH_URL).await;
    let response = client.post(MASHA_AUTH_URL).json(request).send().await?;
    let status = response.status();
    let body = response.bytes().await?;
    let response: AuthorizeResponse = serde_json::from_slice(&body)?;
    if !status.is_success() || !response.allowed {
        let reason = if response.reason.is_empty() {
            "authorization denied"
        } else {
            &response.reason
        };
        bail!("Masha authorization denied: {reason}");
    }
    if response.ticket.is_empty() {
        bail!("Masha authorization returned an empty ticket");
    }
    Ok(response.ticket)
}

pub fn production_public_key() -> Result<sign::PublicKey, TicketError> {
    let bytes = STANDARD
        .decode(MASHA_AUTH_PUBLIC_KEY_B64)
        .map_err(|_| TicketError::InvalidPublicKey)?;
    let bytes: [u8; sign::PUBLICKEYBYTES] = bytes
        .try_into()
        .map_err(|_| TicketError::InvalidPublicKey)?;
    Ok(sign::PublicKey(bytes))
}

pub fn unix_time_now() -> Result<i64, TicketError> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs() as i64)
        .map_err(|_| TicketError::ClockUnavailable)
}

pub fn verify_connection_ticket(
    ticket: &str,
    operator_id: &str,
    target_id: &str,
    session_id: u64,
    path: ConnectionPath,
    target_nonce: &str,
    replay_cache: &ReplayCache,
) -> Result<VerifiedTicket, TicketError> {
    let public_key = production_public_key()?;
    verify_connection_ticket_with_key(
        ticket,
        operator_id,
        target_id,
        session_id,
        path,
        target_nonce,
        unix_time_now()?,
        &public_key,
        replay_cache,
    )
}

fn verify_connection_ticket_with_key(
    ticket: &str,
    operator_id: &str,
    target_id: &str,
    session_id: u64,
    path: ConnectionPath,
    target_nonce: &str,
    now: i64,
    public_key: &sign::PublicKey,
    replay_cache: &ReplayCache,
) -> Result<VerifiedTicket, TicketError> {
    let connection_type = path
        .ticket_value()
        .ok_or(TicketError::UnknownConnectionPath)?;
    let session_id = session_id.to_string();
    let expected = TicketBindings {
        operator_id,
        target_id,
        session_id: &session_id,
        connection_type,
        target_nonce: Some(target_nonce),
    };
    verify_and_consume(ticket, &expected, now, public_key, replay_cache)
}

pub fn verify_and_consume(
    ticket: &str,
    expected: &TicketBindings<'_>,
    now: i64,
    public_key: &sign::PublicKey,
    replay_cache: &ReplayCache,
) -> Result<VerifiedTicket, TicketError> {
    sodiumoxide::init().map_err(|_| TicketError::CryptoUnavailable)?;
    let (payload_text, signature_text) =
        ticket.split_once('.').ok_or(TicketError::InvalidFormat)?;
    if payload_text.is_empty() || signature_text.is_empty() || signature_text.contains('.') {
        return Err(TicketError::InvalidFormat);
    }
    let payload = URL_SAFE_NO_PAD
        .decode(payload_text)
        .map_err(|_| TicketError::InvalidPayloadEncoding)?;
    let signature = URL_SAFE_NO_PAD
        .decode(signature_text)
        .map_err(|_| TicketError::InvalidSignatureEncoding)?;
    if signature.len() != sign::SIGNATUREBYTES {
        return Err(TicketError::InvalidSignatureEncoding);
    }

    let mut signed = Vec::with_capacity(signature.len() + payload.len());
    signed.extend_from_slice(&signature);
    signed.extend_from_slice(&payload);
    let verified_payload =
        sign::verify(&signed, public_key).map_err(|_| TicketError::InvalidSignature)?;
    if verified_payload != payload {
        return Err(TicketError::InvalidSignature);
    }

    let claims: TicketClaims =
        serde_json::from_slice(&payload).map_err(|_| TicketError::InvalidClaims)?;
    validate_claims(&claims, expected, now)?;
    replay_cache.consume(&claims.jti, claims.exp, now)?;

    Ok(VerifiedTicket {
        operator_id: claims.operator_id,
        target_id: claims.target_id,
        session_id: claims.session_id,
        connection_type: claims.connection_type,
        target_nonce: claims.target_nonce,
        jti: claims.jti,
        issued_at: claims.iat,
        expires_at: claims.exp,
    })
}

fn validate_claims(
    claims: &TicketClaims,
    expected: &TicketBindings<'_>,
    now: i64,
) -> Result<(), TicketError> {
    if claims.v != TICKET_VERSION {
        return Err(TicketError::UnsupportedVersion);
    }
    if claims.iss != TICKET_ISSUER {
        return Err(TicketError::InvalidIssuer);
    }
    if claims.iat <= 0
        || claims.exp <= claims.iat
        || claims.exp - claims.iat > MAX_TICKET_TTL_SECONDS
        || !valid_identifier(&claims.operator_id)
        || !valid_identifier(&claims.target_id)
        || !valid_identifier(&claims.session_id)
        || !valid_identifier(&claims.connection_type)
        || !valid_identifier(&claims.client_version)
        || !valid_jti(&claims.jti)
    {
        return Err(TicketError::InvalidClaims);
    }
    if claims.iat > now + MAX_CLOCK_SKEW_SECONDS {
        return Err(TicketError::IssuedInFuture);
    }
    if claims.exp <= now {
        return Err(TicketError::Expired);
    }
    if claims.operator_id != expected.operator_id {
        return Err(TicketError::BindingMismatch("operator_id"));
    }
    if claims.target_id != expected.target_id {
        return Err(TicketError::BindingMismatch("target_id"));
    }
    if claims.session_id != expected.session_id {
        return Err(TicketError::BindingMismatch("session_id"));
    }
    if claims.connection_type != expected.connection_type {
        return Err(TicketError::BindingMismatch("connection_type"));
    }
    if claims.target_nonce.as_deref() != expected.target_nonce {
        return Err(TicketError::BindingMismatch("target_nonce"));
    }
    if claims
        .target_nonce
        .as_deref()
        .is_some_and(|nonce| nonce.is_empty() || nonce.len() > 128)
    {
        return Err(TicketError::InvalidClaims);
    }
    Ok(())
}
fn valid_identifier(value: &str) -> bool {
    !value.is_empty() && value.len() <= 256
}

fn valid_jti(value: &str) -> bool {
    (16..=128).contains(&value.len())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};

    const NOW: i64 = 1_800_000_000;

    fn claims() -> Value {
        json!({
            "v": 1,
            "iss": "masha-auth",
            "operator_id": "operator-01",
            "target_id": "target-01",
            "session_id": "session-01",
            "connection_type": "direct-ip",
            "client_version": "1.4.9",
            "iat": NOW - 5,
            "exp": NOW + 120,
            "jti": "unique-ticket-id-01",
            "target_nonce": "nonce-01"
        })
    }

    fn connection_claims(connection_type: &str, jti: &str) -> Value {
        let mut value = claims();
        value["session_id"] = json!("42");
        value["connection_type"] = json!(connection_type);
        value["jti"] = json!(jti);
        value
    }

    fn bindings() -> TicketBindings<'static> {
        TicketBindings {
            operator_id: "operator-01",
            target_id: "target-01",
            session_id: "session-01",
            connection_type: "direct-ip",
            target_nonce: Some("nonce-01"),
        }
    }

    fn make_ticket(claims: &Value, secret_key: &sign::SecretKey) -> String {
        let payload = serde_json::to_vec(claims).unwrap();
        let signed = sign::sign(&payload, secret_key);
        format!(
            "{}.{}",
            URL_SAFE_NO_PAD.encode(&payload),
            URL_SAFE_NO_PAD.encode(&signed[..sign::SIGNATUREBYTES])
        )
    }

    fn setup() -> (sign::PublicKey, sign::SecretKey) {
        sodiumoxide::init().unwrap();
        sign::gen_keypair()
    }
    #[test]
    fn accepts_valid_ticket() {
        let (public_key, secret_key) = setup();
        let ticket = make_ticket(&claims(), &secret_key);
        let verified = verify_and_consume(
            &ticket,
            &bindings(),
            NOW,
            &public_key,
            &ReplayCache::default(),
        )
        .unwrap();
        assert_eq!(verified.jti, "unique-ticket-id-01");
        assert_eq!(verified.session_id, "session-01");
    }

    #[test]
    fn rejects_invalid_signature() {
        let (public_key, secret_key) = setup();
        let ticket = make_ticket(&claims(), &secret_key);
        let (payload, signature) = ticket.split_once('.').unwrap();
        let mut signature = URL_SAFE_NO_PAD.decode(signature).unwrap();
        signature[0] ^= 1;
        let tampered = format!("{payload}.{}", URL_SAFE_NO_PAD.encode(signature));
        assert_eq!(
            verify_and_consume(
                &tampered,
                &bindings(),
                NOW,
                &public_key,
                &ReplayCache::default(),
            ),
            Err(TicketError::InvalidSignature)
        );
    }

    #[test]
    fn rejects_expired_ticket() {
        let (public_key, secret_key) = setup();
        let mut expired = claims();
        expired["exp"] = json!(NOW);
        let ticket = make_ticket(&expired, &secret_key);
        assert_eq!(
            verify_and_consume(
                &ticket,
                &bindings(),
                NOW,
                &public_key,
                &ReplayCache::default(),
            ),
            Err(TicketError::Expired)
        );
    }

    #[test]
    fn rejects_ticket_issued_in_future() {
        let (public_key, secret_key) = setup();
        let mut future = claims();
        future["iat"] = json!(NOW + MAX_CLOCK_SKEW_SECONDS + 1);
        future["exp"] = json!(NOW + MAX_CLOCK_SKEW_SECONDS + 121);
        let ticket = make_ticket(&future, &secret_key);
        assert_eq!(
            verify_and_consume(
                &ticket,
                &bindings(),
                NOW,
                &public_key,
                &ReplayCache::default(),
            ),
            Err(TicketError::IssuedInFuture)
        );
    }

    #[test]
    fn rejects_wrong_bindings() {
        let (public_key, secret_key) = setup();
        let ticket = make_ticket(&claims(), &secret_key);
        let base = bindings();
        let cases = [
            TicketBindings {
                operator_id: "other-operator",
                ..base
            },
            TicketBindings {
                target_id: "other-target",
                ..base
            },
            TicketBindings {
                session_id: "other-session",
                ..base
            },
            TicketBindings {
                connection_type: "relay",
                ..base
            },
            TicketBindings {
                target_nonce: Some("other-nonce"),
                ..base
            },
        ];
        let expected_errors = [
            TicketError::BindingMismatch("operator_id"),
            TicketError::BindingMismatch("target_id"),
            TicketError::BindingMismatch("session_id"),
            TicketError::BindingMismatch("connection_type"),
            TicketError::BindingMismatch("target_nonce"),
        ];
        for (case, expected_error) in cases.iter().zip(expected_errors) {
            assert_eq!(
                verify_and_consume(&ticket, case, NOW, &public_key, &ReplayCache::default(),),
                Err(expected_error)
            );
        }
    }
    #[test]
    fn rejects_replayed_jti() {
        let (public_key, secret_key) = setup();
        let ticket = make_ticket(&claims(), &secret_key);
        let replay_cache = ReplayCache::default();
        verify_and_consume(&ticket, &bindings(), NOW, &public_key, &replay_cache).unwrap();
        assert_eq!(
            verify_and_consume(&ticket, &bindings(), NOW, &public_key, &replay_cache,),
            Err(TicketError::Replay)
        );
    }

    #[test]
    fn connection_gate_accepts_direct_and_relay_paths() {
        let (public_key, secret_key) = setup();
        let replay_cache = ReplayCache::default();
        let cases = [
            (ConnectionPath::Direct, "direct-ip", "gate-ticket-direct-01"),
            (ConnectionPath::Relay, "relay", "gate-ticket-relay-01"),
        ];
        for (path, connection_type, jti) in cases {
            let ticket = make_ticket(&connection_claims(connection_type, jti), &secret_key);
            let verified = verify_connection_ticket_with_key(
                &ticket,
                "operator-01",
                "target-01",
                42,
                path,
                "nonce-01",
                NOW,
                &public_key,
                &replay_cache,
            )
            .unwrap();
            assert_eq!(verified.connection_type, connection_type);
        }
    }

    #[test]
    fn connection_gate_fails_closed_without_ticket_or_known_path() {
        let (public_key, secret_key) = setup();
        let ticket = make_ticket(
            &connection_claims("direct-ip", "gate-ticket-unknown-01"),
            &secret_key,
        );
        let replay_cache = ReplayCache::default();
        assert_eq!(
            verify_connection_ticket_with_key(
                &ticket,
                "operator-01",
                "target-01",
                42,
                ConnectionPath::Unknown,
                "nonce-01",
                NOW,
                &public_key,
                &replay_cache,
            ),
            Err(TicketError::UnknownConnectionPath)
        );
        assert_eq!(
            verify_connection_ticket_with_key(
                "",
                "operator-01",
                "target-01",
                42,
                ConnectionPath::Direct,
                "nonce-01",
                NOW,
                &public_key,
                &replay_cache,
            ),
            Err(TicketError::InvalidFormat)
        );
    }

    #[test]
    fn connection_gate_rejects_ticket_for_another_path() {
        let (public_key, secret_key) = setup();
        let ticket = make_ticket(
            &connection_claims("relay", "gate-ticket-wrong-path-01"),
            &secret_key,
        );
        assert_eq!(
            verify_connection_ticket_with_key(
                &ticket,
                "operator-01",
                "target-01",
                42,
                ConnectionPath::Direct,
                "nonce-01",
                NOW,
                &public_key,
                &ReplayCache::default(),
            ),
            Err(TicketError::BindingMismatch("connection_type"))
        );
    }

    #[test]
    fn login_envelope_preserves_ticket_and_password() {
        let password = vec![0, 1, 2, 255];
        let envelope = wrap_login_password(password.clone(), "signed.ticket.value");
        let (ticket, restored_password) = unwrap_login_password(&envelope).unwrap();
        assert_eq!(ticket, "signed.ticket.value");
        assert_eq!(restored_password, password);
    }

    #[test]
    fn login_envelope_fails_closed_when_missing_or_truncated() {
        assert_eq!(
            unwrap_login_password(b"ordinary-password"),
            Err(TicketError::InvalidFormat)
        );
        let envelope = wrap_login_password(vec![1, 2, 3], "ticket");
        assert_eq!(
            unwrap_login_password(&envelope[..18]),
            Err(TicketError::InvalidFormat)
        );
    }

    #[test]
    fn production_key_is_valid_ed25519_key() {
        assert!(production_public_key().is_ok());
    }
}

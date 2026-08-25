use hbb_common::{
    anyhow::{anyhow, bail},
    config::Config,
    sodiumoxide::{base64, crypto::sign},
    ResultType,
};
use serde::{Deserialize, Serialize};
use std::time::{SystemTime, UNIX_EPOCH};

pub const TICKET_MESSAGE_TYPE: &str = "masha-session-ticket";
pub const AUTH_URL_OPTION: &str = "masha-auth-url";
pub const AUTH_PUBLIC_KEY_OPTION: &str = "masha-auth-public-key";

// Staged rollout: keep false until end-to-end client/receiver test passes.
pub const ENFORCE_SERVER_AUTH: bool = false;
pub const DEFAULT_AUTH_BASE_URL: &str = "https://77.222.38.70:8443";
pub const DEFAULT_AUTH_PUBLIC_KEY: &str = "ScrTUazLLtnsMXrbZUPcXYcyNWx7JgXS6quKrIpHGy4=";

#[derive(Debug, Clone, Serialize)]
struct AuthorizeRequest {
    operator_id: String,
    target_id: String,
    connection_type: String,
    client_version: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    target_nonce: Option<String>,
}

#[derive(Debug, Deserialize)]
struct AuthorizeResponse {
    allowed: bool,
    ticket: Option<String>,
    reason: Option<String>,
}
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct SessionTicketClaims {
    pub v: u32,
    pub iss: String,
    pub operator_id: String,
    pub target_id: String,
    pub connection_type: String,
    pub client_version: String,
    pub iat: u64,
    pub exp: u64,
    pub jti: String,
    #[serde(default)]
    pub target_nonce: Option<String>,
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

pub fn auth_base_url() -> String {
    let configured = Config::get_option(AUTH_URL_OPTION);
    if configured.trim().is_empty() {
        DEFAULT_AUTH_BASE_URL.to_owned()
    } else {
        configured.trim_end_matches('/').to_owned()
    }
}

pub fn auth_public_key() -> String {
    let configured = Config::get_option(AUTH_PUBLIC_KEY_OPTION);    if configured.trim().is_empty() {
        DEFAULT_AUTH_PUBLIC_KEY.to_owned()
    } else {
        configured.trim().to_owned()
    }
}

pub fn is_enforced() -> bool {
    ENFORCE_SERVER_AUTH
}

pub fn connection_type_for_client(peer: &str, conn_type: hbb_common::rendezvous_proto::ConnType) -> String {
    if hbb_common::is_ip_str(peer) || hbb_common::is_domain_port_str(peer) {
        return "direct-ip".to_owned();
    }
    format!("{:?}", conn_type).to_lowercase()
}

pub async fn request_ticket(
    operator_id: &str,
    target_id: &str,
    connection_type: &str,
    client_version: &str,
    target_nonce: Option<&str>,
) -> ResultType<String> {
    let base = auth_base_url();
    if base.is_empty() {
        bail!("Masha Auth URL is not configured");
    }
    let req = AuthorizeRequest {
        operator_id: operator_id.to_owned(),
        target_id: target_id.to_owned(),
        connection_type: connection_type.to_owned(),
        client_version: client_version.to_owned(),
        target_nonce: target_nonce.map(str::to_owned),
    };    let resp = reqwest::Client::new()
        .post(format!("{base}/v1/session/authorize"))
        .json(&req)
        .send()
        .await?;
    let status = resp.status();
    let body: AuthorizeResponse = resp.json().await?;
    if !status.is_success() || !body.allowed {
        bail!(body.reason.unwrap_or_else(|| format!("HTTP {status}")));
    }
    let ticket = body.ticket.ok_or_else(|| anyhow!("Masha Auth returned no ticket"))?;
    let claims = verify_ticket(&ticket, operator_id, target_id)?;
    if claims.target_nonce.as_deref() != target_nonce {
        bail!("Masha ticket nonce mismatch");
    }
    Ok(ticket)
}

pub fn verify_ticket(ticket: &str, expected_operator: &str, expected_target: &str) -> ResultType<SessionTicketClaims> {
    let (payload_b64, sig_b64) = ticket
        .split_once('.')
        .ok_or_else(|| anyhow!("invalid Masha ticket format"))?;
    let payload = base64::decode(payload_b64, base64::Variant::UrlSafeNoPadding)
        .map_err(|_| anyhow!("invalid Masha ticket payload"))?;
    let sig_bytes = base64::decode(sig_b64, base64::Variant::UrlSafeNoPadding)
        .map_err(|_| anyhow!("invalid Masha ticket signature"))?;
    let pub_bytes = base64::decode(auth_public_key(), base64::Variant::Original)
        .map_err(|_| anyhow!("invalid Masha Auth public key"))?;
    let pk = sign::PublicKey::from_slice(&pub_bytes)
        .ok_or_else(|| anyhow!("invalid Masha Auth public key length"))?;
    let sig = sign::Signature::from_bytes(&sig_bytes)
        .map_err(|_| anyhow!("invalid Masha ticket signature length"))?;    if !sign::verify_detached(&sig, &payload, &pk) {
        bail!("Masha ticket signature verification failed");
    }
    let claims: SessionTicketClaims = serde_json::from_slice(&payload)?;
    if claims.v != 1 || claims.iss != "masha-auth" {
        bail!("unsupported Masha ticket");
    }
    if claims.operator_id != expected_operator {
        bail!("Masha ticket operator mismatch");
    }
    if claims.target_id != expected_target {
        bail!("Masha ticket target mismatch");
    }
    let now = now_secs();
    if claims.exp <= now {
        bail!("Masha ticket expired");
    }
    if claims.iat > now.saturating_add(30) {
        bail!("Masha ticket issued in the future");
    }
    if claims.exp.saturating_sub(claims.iat) > 600 {
        bail!("Masha ticket lifetime too long");
    }
    Ok(claims)
}
pub fn verify_ticket_unbound(ticket: &str) -> ResultType<SessionTicketClaims> {
    let (payload_b64, _) = ticket
        .split_once('.')
        .ok_or_else(|| anyhow!("invalid Masha ticket format"))?;
    let payload = base64::decode(payload_b64, base64::Variant::UrlSafeNoPadding)
        .map_err(|_| anyhow!("invalid Masha ticket payload"))?;
    let claims: SessionTicketClaims = serde_json::from_slice(&payload)?;
    verify_ticket(ticket, &claims.operator_id, &claims.target_id)
}

const HASH_BINDING_MARKER: &str = "|masha1|";

pub fn make_bound_hash_challenge(base: &str, target_id: &str) -> String {
    let target = base64::encode(target_id.as_bytes(), base64::Variant::UrlSafeNoPadding);
    let nonce = uuid::Uuid::new_v4().simple().to_string();
    format!("{base}{HASH_BINDING_MARKER}{target}|{nonce}")
}

pub fn parse_bound_hash_challenge(challenge: &str) -> Option<(String, String)> {
    let (_, meta) = challenge.rsplit_once(HASH_BINDING_MARKER)?;
    let (target_b64, nonce) = meta.split_once('|')?;
    if nonce.len() != 32 || !nonce.bytes().all(|b| b.is_ascii_hexdigit()) { return None; }
    let raw = base64::decode(target_b64, base64::Variant::UrlSafeNoPadding).ok()?;
    let target = String::from_utf8(raw).ok()?;
    if target.is_empty() { None } else { Some((target, nonce.to_owned())) }
}

pub fn claims_valid_now(claims: &SessionTicketClaims) -> bool {
    let now = now_secs();
    claims.exp > now && claims.iat <= now.saturating_add(30)
}
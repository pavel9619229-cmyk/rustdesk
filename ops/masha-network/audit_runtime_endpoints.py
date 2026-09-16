from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
errors = []

def require(path, text, label):
    data = (ROOT / path).read_text(encoding='utf-8')
    if text not in data:
        errors.append(f'{label}: missing in {path}')

def forbid(path, text, label):
    data = (ROOT / path).read_text(encoding='utf-8')
    if text in data:
        errors.append(f'{label}: found in {path}')

require('src/common.rs', 'pub fn masha_service_url_allowed', 'Rust allowlist')
require('src/common.rs', 'ensure_masha_service_url_allowed(&url)?;', 'Rust HTTP guard')
require('src/common.rs', 'ensure_masha_service_url_allowed(url)?;', 'Rust TCP-proxy guard')
require('src/common.rs', 'host == "77.222.38.70"', 'Masha VPS allowlist')
require('src/common.rs', 'host.ends_with(".agentmasha.ru")', 'Masha domain allowlist')
require('src/hbbs_http/http_client.rs', 'redirect(reqwest::redirect::Policy::none())', 'HTTP redirect block')
require('src/auth_2fa.rs', 'Telegram 2FA is disabled in Masha', 'Telegram network disable')
forbid('src/auth_2fa.rs', 'api.telegram.org', 'Telegram endpoint')
forbid('src/common.rs', '"https://admin.rustdesk.com".to_owned()', 'RustDesk API fallback')
require('src/common.rs', 'config::EXE_RENDEZVOUS_SERVER', 'Forced Masha rendezvous')
require('build.py', 'apply_hbb_common_runtime_policy.py', 'Build-time hbb_common hardening')
require('libs/hbb_common/src/config.rs', 'pub const RENDEZVOUS_SERVERS: &[&str] = &["77.222.38.70"];', 'Patched upstream rendezvous')
forbid('libs/hbb_common/src/config.rs', 'rs-ny.rustdesk.com', 'Upstream rendezvous fallback')
forbid('libs/hbb_common/src/config.rs', 'https://rustdesk.com/docs/', 'Upstream docs URL')
forbid('libs/hbb_common/src/config.rs', 'https://github.com/rustdesk/rustdesk/wiki/', 'Upstream GitHub helper URL')
forbid('libs/hbb_common/src/lib.rs', 'https://api.rustdesk.com/version/latest', 'Upstream update URL')

policy = ROOT / 'flutter/lib/masha_network_policy.dart'
if not policy.exists():
    errors.append('Flutter network policy file missing')
else:
    pdata = policy.read_text(encoding='utf-8')
    for token in ('agentmasha.ru', 'yookassa.ru', 'yoomoney.ru'):
        if token not in pdata:
            errors.append(f'Flutter allowlist missing {token}')

for path in (ROOT / 'flutter/lib').rglob('*.dart'):
    if path == policy:
        continue
    data = path.read_text(encoding='utf-8', errors='replace')
    code = '\n'.join(line for line in data.splitlines() if not line.lstrip().startswith('//'))
    if re.search(r'\blaunchUrl\s*\(', code):
        errors.append(f'direct launchUrl bypass: {path.relative_to(ROOT)}')
    if re.search(r'\blaunchUrlString\s*\(', code):
        errors.append(f'direct launchUrlString bypass: {path.relative_to(ROOT)}')

checks = {
    'src/common.rs': ('https://api.rustdesk.com/version/latest',),
    'src/auth_2fa.rs': ('https://api.telegram.org',),
    'flutter/lib/common.dart': ('https://github.com/pavel9619229-cmyk/rustdesk',),
}
for rel, needles in checks.items():
    data = (ROOT / rel).read_text(encoding='utf-8')
    for needle in needles:
        if needle in data:
            errors.append(f'foreign service endpoint remains: {rel}: {needle}')

if errors:
    print('MASHA_NETWORK_POLICY=FAIL')
    for error in errors:
        print('FAIL:', error)
    sys.exit(1)

print('MASHA_NETWORK_POLICY=PASS')
print('Rust service HTTP: Masha allowlist only')
print('HTTP redirects: disabled')
print('Flutter external launches: allowlist wrapper only')
print('RustDesk/GitHub/Telegram service endpoints: disabled/blocked')

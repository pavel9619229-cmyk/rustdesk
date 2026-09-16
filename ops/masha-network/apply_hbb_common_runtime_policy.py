from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'libs' / 'hbb_common' / 'src' / 'config.rs'
LIB = ROOT / 'libs' / 'hbb_common' / 'src' / 'lib.rs'
COMMON = ROOT / 'src' / 'common.rs'

common = COMMON.read_text(encoding='utf-8')
match = re.search(r'pub const MASHA_SERVER_PUBLIC_KEY: &str = "([^"]+)";', common)
if not match:
    raise SystemExit('Masha server public key not found')
masha_key = match.group(1)

config = CONFIG.read_text(encoding='utf-8')
static_pairs = [
    ('https://rustdesk.com/docs/en/', 'https://agentmasha.ru/docs/en/'),
    ('https://rustdesk.com/docs/en/manual/linux/#x11-required',
     'https://agentmasha.ru/docs/en/manual/linux/#x11-required'),
    ('https://github.com/rustdesk/rustdesk/wiki/Headless-Linux-Support',
     'https://agentmasha.ru/wiki/Headless-Linux-Support'),
    ('pub const RENDEZVOUS_SERVERS: &[&str] = &["rs-ny.rustdesk.com"];',
     'pub const RENDEZVOUS_SERVERS: &[&str] = &["77.222.38.70"];'),
]
for old, new in static_pairs:
    if new not in config:
        if old not in config:
            raise SystemExit(f'missing expected upstream token: {old}')
        config = config.replace(old, new)
config = re.sub(
    r'pub const RS_PUB_KEY: &str = "[^"]+";',
    f'pub const RS_PUB_KEY: &str = "{masha_key}";',
    config,
    count=1,
)
CONFIG.write_text(config, encoding='utf-8')

lib = LIB.read_text(encoding='utf-8')
old = 'const URL: &str = "https://api.rustdesk.com/version/latest";'
new = 'const URL: &str = "https://api.agentmasha.ru/version/latest";'
if new not in lib:
    if old not in lib:
        raise SystemExit('upstream version URL not found')
    lib = lib.replace(old, new)
LIB.write_text(lib, encoding='utf-8')

print('HBB_COMMON_RUNTIME_POLICY=PASS')

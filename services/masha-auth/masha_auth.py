#!/usr/bin/env python3
import argparse, base64, hashlib, json, logging, os, secrets, sqlite3, ssl, time
import urllib.error, urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

BASE=Path(os.getenv('MASHA_AUTH_DIR','/opt/masha-auth'))
DATA=BASE/'data'; SECRETS=BASE/'secrets'
DB=Path(os.getenv('MASHA_AUTH_DB',str(DATA/'auth.db')))
KEY=Path(os.getenv('MASHA_AUTH_SIGNING_KEY',str(SECRETS/'signing_key.pem')))
PUB64=BASE/'public_key.b64'; PUBPEM=BASE/'public_key.pem'
DEFAULT_TTL=120; MAX_BODY=16384
LEASE_HEARTBEAT_SECONDS=10; LEASE_GRACE_SECONDS=30
DEFAULT_POSTPAID_POLICY='postpaid-default'
POSTPAID_RATE_MINOR_PER_HOUR=100
POSTPAID_DUE_SECONDS=86400
POSTPAID_GRACE_SECONDS=3600
POSTPAID_WARNING_SECONDS=600
DOWNLOAD_ROOT=Path(os.getenv('MASHA_DOWNLOAD_ROOT','/opt/masha-downloads'))
DOWNLOAD_FILES={
    'masha-build-69-stage-2.0-windows-x64-63271488f.zip':
        '0B256931D01D77D958BD3FEE957A9CE982615026729103AFCE9CC2E48B32CB7C',
    'masha-stage-2.0-frontend-windows-x64-63271488f.zip':
        '0B256931D01D77D958BD3FEE957A9CE982615026729103AFCE9CC2E48B32CB7C',
    'masha-build-72-windows-x64-b28e77862.zip':
        '2B4121446EBAF35AFE167DB03A0CAACCAD349F686C75997D8A553241A0ABBE90',
}
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
LOG=logging.getLogger('masha-auth')

def b64u(b): return base64.urlsafe_b64encode(b).rstrip(b'=').decode()
@contextmanager
def dbc():
    DATA.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DB,timeout=5); c.row_factory=sqlite3.Row
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def table_columns(c,table):
    return {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}

def create_access_grants(c):
    c.execute("CREATE TABLE IF NOT EXISTS access_grants(grant_id TEXT PRIMARY KEY,operator_id TEXT NOT NULL,source_type TEXT NOT NULL CHECK(source_type IN ('payment','ad_reward','trial','promo','admin','postpaid_account')),grant_kind TEXT NOT NULL CHECK(grant_kind IN ('time_credit','unlimited_period','postpaid_account')),quota_seconds INTEGER,starts_at INTEGER NOT NULL,expires_at INTEGER,status TEXT NOT NULL CHECK(status IN ('active','consumed','expired','revoked')),priority INTEGER NOT NULL DEFAULT 100,source_id TEXT,metadata_json TEXT,created_at INTEGER NOT NULL)")

def ensure_access_grants_schema(c):
    schema=c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='access_grants'").fetchone()
    if not schema:
        create_access_grants(c)
        return
    if 'postpaid_account' in (schema['sql'] or '') and 'metadata_json' in table_columns(c,'access_grants'):
        return
    c.execute('DROP INDEX IF EXISTS idx_access_grants_operator')
    c.execute('DROP INDEX IF EXISTS idx_access_grants_source_event')
    c.execute('ALTER TABLE access_grants RENAME TO access_grants_stage1')
    create_access_grants(c)
    old_columns=table_columns(c,'access_grants_stage1')
    metadata='metadata_json' if 'metadata_json' in old_columns else 'NULL'
    c.execute(f"INSERT INTO access_grants(grant_id,operator_id,source_type,grant_kind,quota_seconds,starts_at,expires_at,status,priority,source_id,metadata_json,created_at) SELECT grant_id,operator_id,source_type,grant_kind,quota_seconds,starts_at,expires_at,status,priority,source_id,{metadata},created_at FROM access_grants_stage1")
    c.execute('DROP TABLE access_grants_stage1')

def init_db():
    with dbc() as c:
        now=int(time.time())
        c.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        c.execute("CREATE TABLE IF NOT EXISTS operators(operator_id TEXT PRIMARY KEY,access_status TEXT NOT NULL CHECK(access_status IN ('active','blocked')),note TEXT NOT NULL DEFAULT '',updated_at INTEGER NOT NULL)")
        columns={r['name'] for r in c.execute('PRAGMA table_info(operators)')}
        if 'valid_until' not in columns:
            c.execute('ALTER TABLE operators ADD COLUMN valid_until INTEGER')
        c.execute("INSERT OR IGNORE INTO settings VALUES('new_sessions_enabled','true')")
        c.execute("INSERT OR IGNORE INTO settings VALUES('ticket_ttl_seconds',?)",(str(DEFAULT_TTL),))
        c.execute("INSERT OR IGNORE INTO settings VALUES('max_concurrent_sessions','1')")
        c.execute("CREATE TABLE IF NOT EXISTS leases(lease_id TEXT PRIMARY KEY,jti TEXT NOT NULL UNIQUE,token_hash TEXT NOT NULL,operator_id TEXT NOT NULL,target_id TEXT NOT NULL,session_id TEXT NOT NULL,connection_type TEXT NOT NULL,started_at INTEGER NOT NULL,last_heartbeat INTEGER NOT NULL,finished_at INTEGER,finish_reason TEXT NOT NULL DEFAULT '',duration_seconds INTEGER)")
        ensure_access_grants_schema(c)
        c.execute('CREATE INDEX IF NOT EXISTS idx_access_grants_operator ON access_grants(operator_id,status,starts_at,expires_at)')
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_access_grants_source_event ON access_grants(source_type,source_id) WHERE source_id IS NOT NULL AND source_id!='legacy-operator'")
        c.execute("CREATE TABLE IF NOT EXISTS billing_accounts(operator_id TEXT PRIMARY KEY,billing_status TEXT NOT NULL CHECK(billing_status IN ('current','payment_due','overdue','blocked')),amount_due_minor INTEGER NOT NULL DEFAULT 0,currency TEXT NOT NULL DEFAULT 'RUB',billable_seconds INTEGER NOT NULL DEFAULT 0,due_at INTEGER,grace_until INTEGER,blocked_at INTEGER,updated_at INTEGER NOT NULL)")
        billing_columns=table_columns(c,'billing_accounts')
        billing_additions=(('amount_due_minor','INTEGER NOT NULL DEFAULT 0'),('currency',"TEXT NOT NULL DEFAULT 'RUB'"),('billable_seconds','INTEGER NOT NULL DEFAULT 0'),('due_at','INTEGER'),('grace_until','INTEGER'),('blocked_at','INTEGER'))
        for column,declaration in billing_additions:
            if column not in billing_columns:
                c.execute(f'ALTER TABLE billing_accounts ADD COLUMN {column} {declaration}')
        c.execute("CREATE TABLE IF NOT EXISTS access_policies(policy_id TEXT PRIMARY KEY,name TEXT NOT NULL,mode TEXT NOT NULL CHECK(mode IN ('postpaid','prepaid_time','hybrid','free')),rate_minor_per_hour INTEGER NOT NULL DEFAULT 100,currency TEXT NOT NULL DEFAULT 'RUB',max_concurrent_sessions INTEGER NOT NULL DEFAULT 1,payment_due_seconds INTEGER NOT NULL DEFAULT 86400,grace_seconds INTEGER NOT NULL DEFAULT 3600,warning_seconds INTEGER NOT NULL DEFAULT 600,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS operator_access_policy(operator_id TEXT PRIMARY KEY,policy_id TEXT NOT NULL,valid_from INTEGER,valid_until INTEGER,override_json TEXT)")
        c.execute("INSERT OR IGNORE INTO access_policies(policy_id,name,mode,rate_minor_per_hour,currency,max_concurrent_sessions,payment_due_seconds,grace_seconds,warning_seconds,created_at,updated_at) VALUES(?,?,'postpaid',?,'RUB',1,?,?,?,?,?)",(DEFAULT_POSTPAID_POLICY,'Постоплата 1 рубль в час',POSTPAID_RATE_MINOR_PER_HOUR,POSTPAID_DUE_SECONDS,POSTPAID_GRACE_SECONDS,POSTPAID_WARNING_SECONDS,now,now))
        lease_columns={r['name'] for r in c.execute('PRAGMA table_info(leases)')}
        if 'grant_id' not in lease_columns:
            c.execute('ALTER TABLE leases ADD COLUMN grant_id TEXT')
        if 'grant_source' not in lease_columns:
            c.execute('ALTER TABLE leases ADD COLUMN grant_source TEXT')
        c.execute("CREATE TABLE IF NOT EXISTS usage_sessions(usage_id TEXT PRIMARY KEY,lease_id TEXT NOT NULL UNIQUE,session_id TEXT NOT NULL,operator_id TEXT NOT NULL,target_id TEXT NOT NULL,ticket_jti TEXT NOT NULL UNIQUE,grant_id TEXT,started_at INTEGER NOT NULL,last_heartbeat_at INTEGER NOT NULL,ended_at INTEGER,duration_seconds INTEGER NOT NULL DEFAULT 0,accounted_seconds INTEGER NOT NULL DEFAULT 0,close_reason TEXT NOT NULL DEFAULT '')")
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_session_binding ON usage_sessions(operator_id,session_id)')
        c.execute("CREATE TABLE IF NOT EXISTS grant_consumption(consumption_id TEXT PRIMARY KEY,grant_id TEXT NOT NULL,lease_id TEXT NOT NULL UNIQUE,session_id TEXT NOT NULL,seconds INTEGER NOT NULL CHECK(seconds>=0),idempotency_key TEXT NOT NULL UNIQUE,recorded_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)")
        c.execute('CREATE INDEX IF NOT EXISTS idx_grant_consumption_grant ON grant_consumption(grant_id)')
        c.execute("CREATE TABLE IF NOT EXISTS payment_orders(payment_order_id TEXT PRIMARY KEY,provider TEXT NOT NULL,provider_payment_id TEXT UNIQUE,operator_id TEXT NOT NULL,amount_minor INTEGER NOT NULL CHECK(amount_minor>0),currency TEXT NOT NULL,billable_seconds_snapshot INTEGER NOT NULL CHECK(billable_seconds_snapshot>=0),idempotence_key TEXT NOT NULL UNIQUE,status TEXT NOT NULL CHECK(status IN ('creating','pending','succeeded','canceled','failed')),confirmation_url TEXT,provider_payload_json TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,settled_at INTEGER)")
        c.execute('CREATE INDEX IF NOT EXISTS idx_payment_orders_operator ON payment_orders(operator_id,created_at)')
        c.execute("CREATE TABLE IF NOT EXISTS payment_events(event_id TEXT PRIMARY KEY,provider TEXT NOT NULL,event_type TEXT NOT NULL,provider_object_id TEXT NOT NULL,payload_json TEXT NOT NULL,received_at INTEGER NOT NULL,verified_at INTEGER,processing_status TEXT NOT NULL,result_json TEXT)")
        c.execute('CREATE INDEX IF NOT EXISTS idx_payment_events_object ON payment_events(provider,provider_object_id)')
        c.execute("INSERT OR IGNORE INTO usage_sessions(usage_id,lease_id,session_id,operator_id,target_id,ticket_jti,grant_id,started_at,last_heartbeat_at,ended_at,duration_seconds,accounted_seconds,close_reason) SELECT lease_id,lease_id,session_id,operator_id,target_id,jti,grant_id,started_at,last_heartbeat,finished_at,COALESCE(duration_seconds,0),COALESCE(duration_seconds,0),finish_reason FROM leases")
        for row in c.execute("SELECT operator_id,valid_until FROM operators o WHERE access_status='active' AND NOT EXISTS(SELECT 1 FROM access_grants g WHERE g.operator_id=o.operator_id)").fetchall():
            grant_id='legacy-admin:'+row['operator_id']
            c.execute("INSERT OR IGNORE INTO access_grants(grant_id,operator_id,source_type,grant_kind,starts_at,expires_at,status,priority,source_id,created_at) VALUES(?,?,?,'unlimited_period',?,?,'active',10,'legacy-operator',?)",(grant_id,row['operator_id'],'admin',now,row['valid_until'],now))

def ensure_key():
    SECRETS.mkdir(parents=True,exist_ok=True)
    if KEY.exists():
        k=serialization.load_pem_private_key(KEY.read_bytes(),password=None)
        if not isinstance(k,Ed25519PrivateKey): raise RuntimeError('signing key is not Ed25519')
    else:
        k=Ed25519PrivateKey.generate()
        KEY.write_bytes(k.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        os.chmod(KEY,0o600)
    p=k.public_key()
    raw=p.public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    PUB64.write_text(base64.b64encode(raw).decode()+'\n',encoding='ascii')
    PUBPEM.write_bytes(p.public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    return k

def setting(c,key,default):
    r=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return r['value'] if r else default

def set_setting(key,value):
    with dbc() as c:
        c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))

def set_operator(op,status,note='',valid_until=None):
    now=int(time.time())
    with dbc() as c:
        c.execute('INSERT INTO operators(operator_id,access_status,note,updated_at,valid_until) VALUES(?,?,?,?,?) ON CONFLICT(operator_id) DO UPDATE SET access_status=excluded.access_status,note=excluded.note,updated_at=excluded.updated_at,valid_until=excluded.valid_until',(op,status,note,now,valid_until))
        grant_id='legacy-admin:'+op
        if status=='active':
            c.execute("INSERT INTO access_grants(grant_id,operator_id,source_type,grant_kind,starts_at,expires_at,status,priority,source_id,created_at) VALUES(?,?,?,'unlimited_period',?,?,'active',10,'legacy-operator',?) ON CONFLICT(grant_id) DO UPDATE SET starts_at=excluded.starts_at,expires_at=excluded.expires_at,status='active'",(grant_id,op,'admin',now,valid_until,now))
        else:
            c.execute("UPDATE access_grants SET status='revoked' WHERE grant_id=?",(grant_id,))

def set_grant(operator_id,source_type,grant_kind='unlimited_period',quota_seconds=None,expires_at=None,source_id=None,priority=100):
    if source_type not in ('payment','ad_reward','trial','promo','admin'): raise ValueError('invalid source_type')
    if grant_kind not in ('time_credit','unlimited_period'): raise ValueError('invalid grant_kind')
    if grant_kind=='time_credit' and (quota_seconds is None or int(quota_seconds)<=0): raise ValueError('quota_seconds required')
    now=int(time.time()); grant_id=secrets.token_urlsafe(18)
    with dbc() as c:
        c.execute('BEGIN IMMEDIATE')
        if source_id and source_id!='legacy-operator':
            existing=c.execute('SELECT grant_id,operator_id FROM access_grants WHERE source_type=? AND source_id=?',(source_type,source_id)).fetchone()
            if existing:
                if existing['operator_id']!=operator_id: raise ValueError('source event belongs to another operator')
                return existing['grant_id']
        c.execute("INSERT OR IGNORE INTO operators(operator_id,access_status,note,updated_at,valid_until) VALUES(?,'active','created by grant',?,NULL)",(operator_id,now))
        c.execute("INSERT INTO access_grants(grant_id,operator_id,source_type,grant_kind,quota_seconds,starts_at,expires_at,status,priority,source_id,created_at) VALUES(?,?,?,?,?,?,?,'active',?,?,?)",(grant_id,operator_id,source_type,grant_kind,quota_seconds,now,expires_at,priority,source_id,now))
    return grant_id

def set_billing(operator_id,status):
    if status not in ('current','payment_due','overdue','blocked'): raise ValueError('invalid billing status')
    with dbc() as c:
        now=int(time.time())
        blocked_at=now if status=='blocked' else None
        c.execute('INSERT INTO billing_accounts(operator_id,billing_status,blocked_at,updated_at) VALUES(?,?,?,?) ON CONFLICT(operator_id) DO UPDATE SET billing_status=excluded.billing_status,blocked_at=excluded.blocked_at,updated_at=excluded.updated_at',(operator_id,status,blocked_at,now))

def set_postpaid_policy(policy_id=DEFAULT_POSTPAID_POLICY,rate_minor_per_hour=POSTPAID_RATE_MINOR_PER_HOUR,payment_due_seconds=POSTPAID_DUE_SECONDS,grace_seconds=POSTPAID_GRACE_SECONDS,warning_seconds=POSTPAID_WARNING_SECONDS,max_concurrent_sessions=1):
    values=(int(rate_minor_per_hour),int(payment_due_seconds),int(grace_seconds),int(warning_seconds),int(max_concurrent_sessions))
    if values[0]<=0: raise ValueError('rate_minor_per_hour must be positive')
    if min(values[1:4])<0: raise ValueError('billing intervals must be non-negative')
    if values[3]>values[1]+values[2]: raise ValueError('warning_seconds exceeds time to block')
    if values[4]<1 or values[4]>100: raise ValueError('max_concurrent_sessions must be 1..100')
    now=int(time.time())
    with dbc() as c:
        c.execute("INSERT INTO access_policies(policy_id,name,mode,rate_minor_per_hour,currency,max_concurrent_sessions,payment_due_seconds,grace_seconds,warning_seconds,created_at,updated_at) VALUES(?,?,'postpaid',?,'RUB',?,?,?,?,?,?) ON CONFLICT(policy_id) DO UPDATE SET rate_minor_per_hour=excluded.rate_minor_per_hour,max_concurrent_sessions=excluded.max_concurrent_sessions,payment_due_seconds=excluded.payment_due_seconds,grace_seconds=excluded.grace_seconds,warning_seconds=excluded.warning_seconds,updated_at=excluded.updated_at",(policy_id,'Постоплата',values[0],values[4],values[1],values[2],values[3],now,now))

def enable_postpaid(operator_id,policy_id=DEFAULT_POSTPAID_POLICY):
    if not operator_id: raise ValueError('operator_id required')
    now=int(time.time()); grant_id='postpaid:'+operator_id
    with dbc() as c:
        c.execute('BEGIN IMMEDIATE')
        policy=c.execute("SELECT policy_id FROM access_policies WHERE policy_id=? AND mode IN ('postpaid','hybrid')",(policy_id,)).fetchone()
        if not policy: raise ValueError('postpaid policy not found')
        c.execute("INSERT OR IGNORE INTO operators(operator_id,access_status,note,updated_at,valid_until) VALUES(?,'active','postpaid account',?,NULL)",(operator_id,now))
        c.execute("UPDATE access_grants SET status='revoked' WHERE grant_id=? AND status='active'",('legacy-admin:'+operator_id,))
        c.execute("INSERT INTO operator_access_policy(operator_id,policy_id,valid_from,valid_until,override_json) VALUES(?,?,?,NULL,NULL) ON CONFLICT(operator_id) DO UPDATE SET policy_id=excluded.policy_id,valid_from=excluded.valid_from,valid_until=NULL,override_json=NULL",(operator_id,policy_id,now))
        c.execute("INSERT OR IGNORE INTO billing_accounts(operator_id,billing_status,amount_due_minor,currency,billable_seconds,updated_at) VALUES(?,'current',0,'RUB',0,?)",(operator_id,now))
        metadata=json.dumps({'policy_id':policy_id},sort_keys=True,separators=(',',':'))
        c.execute("INSERT INTO access_grants(grant_id,operator_id,source_type,grant_kind,quota_seconds,starts_at,expires_at,status,priority,source_id,metadata_json,created_at) VALUES(?,?,?,'postpaid_account',NULL,?,NULL,'active',500,?,?,?) ON CONFLICT(grant_id) DO UPDATE SET starts_at=excluded.starts_at,status='active',metadata_json=excluded.metadata_json",(grant_id,operator_id,'postpaid_account',now,'postpaid-account:'+operator_id,metadata,now))
    return grant_id

def settle_postpaid(operator_id):
    now=int(time.time())
    with dbc() as c:
        changed=c.execute("UPDATE billing_accounts SET billing_status='current',amount_due_minor=0,billable_seconds=0,due_at=NULL,grace_until=NULL,blocked_at=NULL,updated_at=? WHERE operator_id=?",(now,operator_id)).rowcount
    return bool(changed)

def policy_for_operator(c,operator_id,now):
    return c.execute("SELECT p.* FROM operator_access_policy op JOIN access_policies p ON p.policy_id=op.policy_id WHERE op.operator_id=? AND (op.valid_from IS NULL OR op.valid_from<=?) AND (op.valid_until IS NULL OR op.valid_until>?)",(operator_id,now,now)).fetchone()

def refresh_billing_account(c,operator_id,now):
    account=c.execute('SELECT * FROM billing_accounts WHERE operator_id=?',(operator_id,)).fetchone()
    if not account: return None
    status=account['billing_status']; blocked_at=account['blocked_at']
    if int(account['amount_due_minor'] or 0)<=0 and status!='blocked':
        status='current'
    elif status!='blocked' and account['grace_until'] is not None and int(account['grace_until'])<=now:
        status='blocked'; blocked_at=now
    elif status!='blocked' and account['due_at'] is not None and int(account['due_at'])<=now:
        status='overdue'
    elif status not in ('blocked','overdue'):
        status='payment_due'
    if status!=account['billing_status'] or blocked_at!=account['blocked_at']:
        c.execute('UPDATE billing_accounts SET billing_status=?,blocked_at=?,updated_at=? WHERE operator_id=?',(status,blocked_at,now,operator_id))
        account=c.execute('SELECT * FROM billing_accounts WHERE operator_id=?',(operator_id,)).fetchone()
    return account

def accrue_postpaid(c,operator_id,seconds,now):
    seconds=max(0,int(seconds))
    policy=policy_for_operator(c,operator_id,now)
    if not policy: raise ValueError('postpaid policy missing')
    c.execute("INSERT OR IGNORE INTO billing_accounts(operator_id,billing_status,amount_due_minor,currency,billable_seconds,updated_at) VALUES(?,'current',0,?,0,?)",(operator_id,policy['currency'],now))
    account=c.execute('SELECT * FROM billing_accounts WHERE operator_id=?',(operator_id,)).fetchone()
    total_seconds=int(account['billable_seconds'] or 0)+seconds
    amount_due=total_seconds*int(policy['rate_minor_per_hour'])//3600
    due_at=account['due_at']; grace_until=account['grace_until']; status=account['billing_status']
    if amount_due>0 and due_at is None:
        due_at=now+int(policy['payment_due_seconds'])
        grace_until=due_at+int(policy['grace_seconds'])
        status='payment_due'
    c.execute('UPDATE billing_accounts SET billing_status=?,amount_due_minor=?,currency=?,billable_seconds=?,due_at=?,grace_until=?,updated_at=? WHERE operator_id=?',(status,amount_due,policy['currency'],total_seconds,due_at,grace_until,now,operator_id))
    return refresh_billing_account(c,operator_id,now)

def access_status(operator_id,now=None):
    now=int(time.time()) if now is None else int(now)
    with dbc() as c:
        grant,reason=entitlement(c,operator_id,now)
        policy=policy_for_operator(c,operator_id,now)
        account=refresh_billing_account(c,operator_id,now)
        balance=c.execute("SELECT COALESCE(SUM(quota_seconds),0) AS balance FROM access_grants WHERE operator_id=? AND status='active' AND grant_kind='time_credit' AND starts_at<=? AND (expires_at IS NULL OR expires_at>?)",(operator_id,now,now)).fetchone()['balance']
        seconds_until_block=None; warning=False; warning_at=None
        if policy and account and account['grace_until'] is not None:
            seconds_until_block=max(0,int(account['grace_until'])-now)
            warning_at=int(account['grace_until'])-int(policy['warning_seconds'])
            warning=account['billing_status']!='blocked' and seconds_until_block<=int(policy['warning_seconds'])
        return {'allowed':reason is None,'reason':'allowed' if reason is None else reason,'policy_mode':policy['mode'] if policy else None,'grant_id':grant['grant_id'] if grant else None,'grant_source':grant['source_type'] if grant else None,'balance_seconds':int(balance or 0),'valid_until':grant['expires_at'] if grant else None,'billing_status':account['billing_status'] if account else None,'amount_due_minor':int(account['amount_due_minor'] or 0) if account else 0,'currency':account['currency'] if account else 'RUB','due_at':account['due_at'] if account else None,'grace_until':account['grace_until'] if account else None,'blocked_at':account['blocked_at'] if account else None,'warning_at':warning_at,'warning_10_minutes':warning,'seconds_until_block':seconds_until_block,'policy_id':policy['policy_id'] if policy else None,'rate_minor_per_hour':int(policy['rate_minor_per_hour']) if policy else None,'payment_due_seconds':int(policy['payment_due_seconds']) if policy else None,'grace_seconds':int(policy['grace_seconds']) if policy else None,'warning_seconds':int(policy['warning_seconds']) if policy else None,'billable_seconds':int(account['billable_seconds'] or 0) if account else 0,'server_time':now}


class PaymentError(RuntimeError):
    def __init__(self,http_status,reason,retryable=False):
        super().__init__(reason)
        self.http_status=int(http_status)
        self.reason=str(reason)
        self.retryable=bool(retryable)

def yookassa_config():
    try: timeout=max(2,min(int(os.getenv('YOOKASSA_TIMEOUT_SECONDS','10')),30))
    except ValueError: timeout=10
    public_base=os.getenv('MASHA_PUBLIC_BASE_URL','').strip().rstrip('/')
    return_url=os.getenv('YOOKASSA_RETURN_URL','').strip()
    if not return_url and public_base:
        return_url=public_base+'/v1/payments/return'
    return {
        'api_base':os.getenv('YOOKASSA_API_BASE','https://api.yookassa.ru/v3').strip().rstrip('/'),
        'shop_id':os.getenv('YOOKASSA_SHOP_ID','').strip(),
        'secret_key':os.getenv('YOOKASSA_SECRET_KEY','').strip(),
        'return_url':return_url,
        'timeout':timeout,
        'receipt_mode':os.getenv('YOOKASSA_RECEIPT_MODE','none').strip().lower(),
        'vat_code':os.getenv('YOOKASSA_VAT_CODE','').strip(),
        'tax_system_code':os.getenv('YOOKASSA_TAX_SYSTEM_CODE','').strip(),
    }

def yookassa_ready():
    cfg=yookassa_config()
    return bool(cfg['shop_id'] and cfg['secret_key'] and cfg['return_url'])

def money_minor_to_value(amount_minor):
    amount_minor=int(amount_minor)
    return f'{amount_minor//100}.{amount_minor%100:02d}'

def money_value_to_minor(value):
    try: amount=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError) as exc: raise ValueError('invalid money value') from exc
    minor=amount*100
    if minor!=minor.to_integral_value(): raise ValueError('money value has sub-kopeck precision')
    return int(minor)

def yookassa_api(method,path,payload=None,idempotence_key=None):
    cfg=yookassa_config()
    if not cfg['shop_id'] or not cfg['secret_key']:
        raise PaymentError(503,'provider_not_configured')
    token=base64.b64encode(f"{cfg['shop_id']}:{cfg['secret_key']}".encode()).decode()
    headers={'Authorization':'Basic '+token,'Accept':'application/json'}
    data=None
    if payload is not None:
        data=json.dumps(payload,separators=(',',':'),ensure_ascii=False).encode()
        headers['Content-Type']='application/json'
    if idempotence_key: headers['Idempotence-Key']=idempotence_key
    request=urllib.request.Request(cfg['api_base']+'/'+path.lstrip('/'),data=data,headers=headers,method=method)
    try:
        with urllib.request.urlopen(request,timeout=cfg['timeout']) as response:
            raw=response.read()
    except urllib.error.HTTPError as exc:
        raw=exc.read(4096).decode(errors='replace')
        LOG.warning('YooKassa HTTP error status=%s body=%s',exc.code,raw[:500])
        raise PaymentError(502,'provider_http_error',retryable=exc.code>=500) from exc
    except (urllib.error.URLError,TimeoutError,OSError) as exc:
        LOG.warning('YooKassa unavailable: %s',type(exc).__name__)
        raise PaymentError(503,'provider_unavailable',retryable=True) from exc
    try: result=json.loads(raw or b'{}')
    except Exception as exc: raise PaymentError(502,'provider_invalid_response',retryable=True) from exc
    if not isinstance(result,dict): raise PaymentError(502,'provider_invalid_response',retryable=True)
    return result

def yookassa_get_payment(provider_payment_id):
    return yookassa_api('GET','/payments/'+provider_payment_id)

def _payment_row_payload(row,reused=False):
    return {'payment_order_id':row['payment_order_id'],'provider':'yookassa','provider_payment_id':row['provider_payment_id'],'status':row['status'],'amount_minor':int(row['amount_minor']),'currency':row['currency'],'confirmation_url':row['confirmation_url'],'reused':bool(reused),'created_at':int(row['created_at']),'settled_at':row['settled_at']}

def _validate_receipt_email(value):
    return isinstance(value,str) and 3<=len(value)<=254 and '@' in value and not any(ch.isspace() for ch in value)

def _payment_receipt(req,amount_minor,currency,cfg):
    if cfg['receipt_mode'] in ('','none','off','disabled'): return None
    if cfg['receipt_mode']!='yookassa': raise PaymentError(503,'receipt_mode_invalid')
    email=req.get('receipt_email','')
    if not _validate_receipt_email(email): raise PaymentError(400,'receipt_email_required')
    try: vat_code=int(cfg['vat_code'])
    except ValueError as exc: raise PaymentError(503,'receipt_vat_not_configured') from exc
    if vat_code<1 or vat_code>12: raise PaymentError(503,'receipt_vat_not_configured')
    receipt={'customer':{'email':email.strip()},'items':[{'description':'????????? ?????? ????','quantity':1,'amount':{'value':money_minor_to_value(amount_minor),'currency':currency},'vat_code':vat_code,'payment_mode':'full_payment','payment_subject':'service'}],'internet':True}
    if cfg['tax_system_code']:
        try: receipt['tax_system_code']=int(cfg['tax_system_code'])
        except ValueError as exc: raise PaymentError(503,'receipt_tax_system_invalid') from exc
    return receipt

def create_yookassa_payment(req):
    cfg=yookassa_config()
    if not (cfg['shop_id'] and cfg['secret_key'] and cfg['return_url']): raise PaymentError(503,'provider_not_configured')
    operator_id=req.get('operator_id','')
    if not isinstance(operator_id,str) or not operator_id.strip() or len(operator_id)>256: raise PaymentError(400,'invalid_operator_id')
    operator_id=operator_id.strip(); now=int(time.time())
    with dbc() as c:
        policy=policy_for_operator(c,operator_id,now)
        account=refresh_billing_account(c,operator_id,now)
        if not policy or not account: raise PaymentError(409,'postpaid_not_enabled')
        amount_minor=int(account['amount_due_minor'] or 0); currency=account['currency'] or 'RUB'
        snapshot=int(account['billable_seconds'] or 0)
        if amount_minor<=0: raise PaymentError(409,'nothing_to_pay')
        existing=c.execute("SELECT * FROM payment_orders WHERE operator_id=? AND amount_minor=? AND billable_seconds_snapshot=? AND status='pending' AND confirmation_url IS NOT NULL AND updated_at>=? ORDER BY created_at DESC LIMIT 1",(operator_id,amount_minor,snapshot,now-1800)).fetchone()
        if existing: return _payment_row_payload(existing,True)
        receipt=_payment_receipt(req,amount_minor,currency,cfg)
        payment_order_id=secrets.token_urlsafe(18); idempotence_key=secrets.token_hex(16)
        c.execute("INSERT INTO payment_orders(payment_order_id,provider,provider_payment_id,operator_id,amount_minor,currency,billable_seconds_snapshot,idempotence_key,status,created_at,updated_at) VALUES(?,'yookassa',NULL,?,?,?,?,?,'creating',?,?)",(payment_order_id,operator_id,amount_minor,currency,snapshot,idempotence_key,now,now))
    payload={'amount':{'value':money_minor_to_value(amount_minor),'currency':currency},'capture':True,'confirmation':{'type':'redirect','return_url':cfg['return_url']},'description':f'?????? ?????????? ??????? ????, ???????? {operator_id}'[:128],'metadata':{'operator_id':operator_id,'payment_order_id':payment_order_id,'billable_seconds_snapshot':str(snapshot)}}
    if receipt is not None: payload['receipt']=receipt
    try: provider=yookassa_api('POST','/payments',payload,idempotence_key)
    except PaymentError:
        with dbc() as c: c.execute("UPDATE payment_orders SET status='failed',updated_at=? WHERE payment_order_id=? AND provider_payment_id IS NULL",(int(time.time()),payment_order_id))
        raise
    provider_id=provider.get('id'); provider_status=provider.get('status')
    if not isinstance(provider_id,str) or not provider_id or provider_status not in ('pending','succeeded','canceled','waiting_for_capture'):
        raise PaymentError(502,'provider_invalid_response',retryable=True)
    try: provider_amount=money_value_to_minor(provider.get('amount',{}).get('value'))
    except Exception as exc: raise PaymentError(502,'provider_invalid_response',retryable=True) from exc
    if provider_amount!=amount_minor or provider.get('amount',{}).get('currency')!=currency: raise PaymentError(502,'provider_amount_mismatch')
    confirmation_url=(provider.get('confirmation') or {}).get('confirmation_url')
    local_status='canceled' if provider_status=='canceled' else ('succeeded' if provider_status=='succeeded' and provider.get('paid') is True else 'pending')
    with dbc() as c:
        c.execute('UPDATE payment_orders SET provider_payment_id=?,status=?,confirmation_url=?,provider_payload_json=?,updated_at=? WHERE payment_order_id=?',(provider_id,local_status,confirmation_url,json.dumps(provider,ensure_ascii=False,separators=(',',':')),int(time.time()),payment_order_id))
        row=c.execute('SELECT * FROM payment_orders WHERE payment_order_id=?',(payment_order_id,)).fetchone()
    if local_status=='succeeded':
        reconcile_yookassa_payment(provider)
        with dbc() as c: row=c.execute('SELECT * FROM payment_orders WHERE payment_order_id=?',(payment_order_id,)).fetchone()
    return _payment_row_payload(row,False)

def _verify_provider_matches_order(provider,order):
    amount=provider.get('amount') or {}; metadata=provider.get('metadata') or {}
    try: provider_minor=money_value_to_minor(amount.get('value'))
    except Exception as exc: raise PaymentError(409,'payment_amount_invalid') from exc
    if provider_minor!=int(order['amount_minor']) or amount.get('currency')!=order['currency']: raise PaymentError(409,'payment_amount_mismatch')
    if metadata.get('operator_id')!=order['operator_id'] or metadata.get('payment_order_id')!=order['payment_order_id']: raise PaymentError(409,'payment_metadata_mismatch')

def reconcile_yookassa_payment(provider):
    provider_id=provider.get('id')
    if not isinstance(provider_id,str) or not provider_id: raise PaymentError(400,'payment_id_missing')
    now=int(time.time())
    with dbc() as c:
        order=c.execute("SELECT * FROM payment_orders WHERE provider='yookassa' AND provider_payment_id=?",(provider_id,)).fetchone()
        if not order: return {'processed':False,'reason':'unknown_payment','provider_payment_id':provider_id}
        _verify_provider_matches_order(provider,order)
        provider_status=provider.get('status')
        if provider_status=='canceled':
            c.execute("UPDATE payment_orders SET status='canceled',provider_payload_json=?,updated_at=? WHERE payment_order_id=?",(json.dumps(provider,ensure_ascii=False,separators=(',',':')),now,order['payment_order_id']))
            return {'processed':False,'reason':'payment_canceled','payment_order_id':order['payment_order_id']}
        if provider_status!='succeeded' or provider.get('paid') is not True:
            c.execute("UPDATE payment_orders SET status='pending',provider_payload_json=?,updated_at=? WHERE payment_order_id=? AND settled_at IS NULL",(json.dumps(provider,ensure_ascii=False,separators=(',',':')),now,order['payment_order_id']))
            return {'processed':False,'reason':'payment_not_succeeded','payment_order_id':order['payment_order_id']}
        if order['settled_at'] is not None: return {'processed':False,'reason':'already_settled','payment_order_id':order['payment_order_id']}
        c.execute('BEGIN IMMEDIATE')
        order=c.execute('SELECT * FROM payment_orders WHERE payment_order_id=?',(order['payment_order_id'],)).fetchone()
        if order['settled_at'] is not None: return {'processed':False,'reason':'already_settled','payment_order_id':order['payment_order_id']}
        account=c.execute('SELECT * FROM billing_accounts WHERE operator_id=?',(order['operator_id'],)).fetchone()
        policy=policy_for_operator(c,order['operator_id'],now)
        if not account or not policy: raise PaymentError(409,'postpaid_account_missing')
        remaining_seconds=max(0,int(account['billable_seconds'] or 0)-int(order['billable_seconds_snapshot']))
        remaining_minor=remaining_seconds*int(policy['rate_minor_per_hour'])//3600
        if remaining_minor>0:
            billing_status='payment_due'; due_at=now+int(policy['payment_due_seconds']); grace_until=due_at+int(policy['grace_seconds'])
        else:
            billing_status='current'; due_at=None; grace_until=None
        c.execute('UPDATE billing_accounts SET billing_status=?,amount_due_minor=?,billable_seconds=?,due_at=?,grace_until=?,blocked_at=NULL,updated_at=? WHERE operator_id=?',(billing_status,remaining_minor,remaining_seconds,due_at,grace_until,now,order['operator_id']))
        c.execute("UPDATE payment_orders SET status='succeeded',provider_payload_json=?,updated_at=?,settled_at=? WHERE payment_order_id=?",(json.dumps(provider,ensure_ascii=False,separators=(',',':')),now,now,order['payment_order_id']))
        operator_id=order['operator_id']; payment_order_id=order['payment_order_id']
    return {'processed':True,'reason':'payment_applied','payment_order_id':payment_order_id,'operator_id':operator_id,'access':access_status(operator_id)}

def payment_order_status(payment_order_id):
    if not isinstance(payment_order_id,str) or not payment_order_id or len(payment_order_id)>256: raise PaymentError(400,'invalid_payment_order_id')
    with dbc() as c:
        row=c.execute('SELECT * FROM payment_orders WHERE payment_order_id=?',(payment_order_id,)).fetchone()
    if not row: raise PaymentError(404,'payment_not_found')
    result=_payment_row_payload(row,False); result['access']=access_status(row['operator_id']); return result

def sync_yookassa_payment(req):
    payment_order_id=req.get('payment_order_id','')
    if not isinstance(payment_order_id,str) or not payment_order_id or len(payment_order_id)>256: raise PaymentError(400,'invalid_payment_order_id')
    with dbc() as c: order=c.execute('SELECT * FROM payment_orders WHERE payment_order_id=?',(payment_order_id,)).fetchone()
    if not order: raise PaymentError(404,'payment_not_found')
    if not order['provider_payment_id']: raise PaymentError(409,'payment_not_created')
    provider=yookassa_get_payment(order['provider_payment_id'])
    result=reconcile_yookassa_payment(provider); result['payment']=payment_order_status(payment_order_id); return result

def process_yookassa_webhook(body):
    if body.get('type')!='notification': raise PaymentError(400,'invalid_notification')
    event=str(body.get('event',''))
    obj=body.get('object')
    if not isinstance(obj,dict) or not isinstance(obj.get('id'),str) or not obj.get('id'): raise PaymentError(400,'payment_id_missing')
    provider_id=obj['id']; event_id='yookassa:'+event+':'+provider_id; now=int(time.time())
    raw=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    with dbc() as c:
        existing=c.execute('SELECT processing_status,result_json FROM payment_events WHERE event_id=?',(event_id,)).fetchone()
        if existing and existing['processing_status'] in ('applied','ignored','rejected'):
            return {'accepted':True,'duplicate':True,'result':json.loads(existing['result_json'] or '{}')}
        c.execute("INSERT INTO payment_events(event_id,provider,event_type,provider_object_id,payload_json,received_at,processing_status) VALUES(?,'yookassa',?,?,?,?, 'received') ON CONFLICT(event_id) DO UPDATE SET payload_json=excluded.payload_json,received_at=excluded.received_at",(event_id,event,provider_id,raw,now))
    if event not in ('payment.succeeded','payment.canceled','payment.waiting_for_capture'):
        result={'processed':False,'reason':'event_ignored'}
        with dbc() as c: c.execute("UPDATE payment_events SET processing_status='ignored',result_json=? WHERE event_id=?",(json.dumps(result,separators=(',',':')),event_id))
        return {'accepted':True,'duplicate':False,'result':result}
    try: provider=yookassa_get_payment(provider_id)
    except PaymentError as exc:
        with dbc() as c: c.execute("UPDATE payment_events SET processing_status='verification_failed',result_json=? WHERE event_id=?",(json.dumps({'reason':exc.reason},separators=(',',':')),event_id))
        raise
    if provider.get('id')!=provider_id:
        result={'processed':False,'reason':'provider_id_mismatch'}; status='rejected'
    else:
        try:
            result=reconcile_yookassa_payment(provider); status='applied' if result.get('processed') or result.get('reason')=='already_settled' else 'ignored'
        except PaymentError as exc:
            result={'processed':False,'reason':exc.reason}; status='rejected'
    with dbc() as c:
        c.execute('UPDATE payment_events SET verified_at=?,processing_status=?,result_json=? WHERE event_id=?',(int(time.time()),status,json.dumps(result,ensure_ascii=False,separators=(',',':')),event_id))
    return {'accepted':True,'duplicate':False,'result':result}

def authorize(k,req):
    for f in ('operator_id','target_id','session_id','connection_type','client_version'):
        v=req.get(f)
        if not isinstance(v,str) or not v.strip() or len(v)>256:
            return False,'invalid_request',None,None
    op=req['operator_id'].strip(); target=req['target_id'].strip()
    session=req['session_id'].strip(); ctype=req['connection_type'].strip()
    ver=req['client_version'].strip()
    nonce=req.get('target_nonce')
    if nonce is not None:
        if not isinstance(nonce,str) or not nonce.strip() or len(nonce)>128:
            return False,'invalid_target_nonce',None,None
        nonce=nonce.strip()
    if ctype=='direct-ip' and not nonce:
        return False,'target_nonce_required',None,None
    now=int(time.time())
    with dbc() as c:
        if setting(c,'new_sessions_enabled','true').lower()!='true':
            return False,'new_sessions_disabled',None,None
        grant,reason=entitlement(c,op,now)
        if reason:
            return False,reason,None,None
        try: ttl=int(setting(c,'ticket_ttl_seconds',str(DEFAULT_TTL)))
        except ValueError: ttl=DEFAULT_TTL
    ttl=max(30,min(ttl,600)); exp=now+ttl
    claims={'v':1,'iss':'masha-auth','operator_id':op,'target_id':target,'session_id':session,'connection_type':ctype,'client_version':ver,'grant_id':grant['grant_id'],'grant_source':grant['source_type'],'iat':now,'exp':exp,'jti':secrets.token_urlsafe(18)}
    if nonce:
        claims['target_nonce']=nonce
    payload=json.dumps(claims,sort_keys=True,separators=(',',':')).encode()
    ticket=b64u(payload)+'.'+b64u(k.sign(payload))
    return True,'allowed',ticket,exp

def b64ud(value):
    return base64.urlsafe_b64decode(value+'='*(-len(value)%4))

def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()

def entitlement(c,operator_id,now):
    row=c.execute('SELECT access_status,valid_until FROM operators WHERE operator_id=?',(operator_id,)).fetchone()
    if row and row['access_status']=='blocked': return None,'operator_blocked'
    c.execute("UPDATE access_grants SET status='expired' WHERE operator_id=? AND status='active' AND expires_at IS NOT NULL AND expires_at<=?",(operator_id,now))
    grants=c.execute("SELECT grant_id,source_type,grant_kind,quota_seconds,expires_at FROM access_grants WHERE operator_id=? AND status='active' AND starts_at<=? AND (expires_at IS NULL OR expires_at>?) AND (grant_kind IN ('unlimited_period','postpaid_account') OR quota_seconds>0) ORDER BY priority,expires_at IS NULL,expires_at,created_at",(operator_id,now,now)).fetchall()
    postpaid_blocked=False
    for grant in grants:
        if grant['grant_kind']=='postpaid_account':
            policy=policy_for_operator(c,operator_id,now)
            account=refresh_billing_account(c,operator_id,now)
            if not policy or policy['mode'] not in ('postpaid','hybrid'):
                continue
            if account and account['billing_status']=='blocked':
                postpaid_blocked=True
                continue
        return grant,None
    if not row: return None,'operator_unknown'
    if row['valid_until'] is not None and int(row['valid_until'])<=now: return None,'operator_expired'
    if postpaid_blocked: return None,'payment_required'
    billing=c.execute('SELECT billing_status FROM billing_accounts WHERE operator_id=?',(operator_id,)).fetchone()
    if billing and billing['billing_status'] in ('payment_due','overdue','blocked'): return None,'payment_required'
    return None,'no_active_grant'

def ticket_grant(c,claims,now):
    grant=c.execute("SELECT grant_id,source_type,grant_kind FROM access_grants WHERE grant_id=? AND operator_id=? AND source_type=? AND status='active' AND starts_at<=? AND (expires_at IS NULL OR expires_at>?) AND (grant_kind IN ('unlimited_period','postpaid_account') OR quota_seconds>0)",(claims['grant_id'],claims['operator_id'],claims['grant_source'],now,now)).fetchone()
    if not grant: return None
    if grant['grant_kind']=='postpaid_account':
        policy=policy_for_operator(c,claims['operator_id'],now)
        account=refresh_billing_account(c,claims['operator_id'],now)
        if not policy or policy['mode'] not in ('postpaid','hybrid') or (account and account['billing_status']=='blocked'):
            return None
    return grant

def operator_reason(c,operator_id,now):
    return entitlement(c,operator_id,now)[1]

def account_usage(c,lease,account_at):
    usage=c.execute('SELECT * FROM usage_sessions WHERE lease_id=?',(lease['lease_id'],)).fetchone()
    if not usage:
        return max(0,account_at-lease['started_at']),False
    desired=max(0,int(account_at)-int(usage['started_at']))
    accounted=max(0,int(usage['accounted_seconds']))
    delta=max(0,desired-accounted)
    grant=c.execute('SELECT grant_kind,quota_seconds,status FROM access_grants WHERE grant_id=?',(usage['grant_id'],)).fetchone()
    if delta==0:
        exhausted=bool(grant and grant['grant_kind']=='time_credit' and int(grant['quota_seconds'] or 0)<=0)
        return accounted,exhausted
    if grant and grant['grant_kind']=='postpaid_account':
        new_accounted=accounted+delta
        accrue_postpaid(c,usage['operator_id'],delta,int(account_at))
        c.execute('UPDATE usage_sessions SET duration_seconds=?,accounted_seconds=? WHERE lease_id=?',(new_accounted,new_accounted,lease['lease_id']))
        return new_accounted,False
    consumed=delta; exhausted=False
    if grant and grant['grant_kind']=='time_credit':
        available=max(0,int(grant['quota_seconds'] or 0))
        consumed=min(delta,available)
        remaining=available-consumed
        exhausted=remaining==0
        c.execute("UPDATE access_grants SET quota_seconds=?,status=CASE WHEN ?=0 AND status='active' THEN 'consumed' ELSE status END WHERE grant_id=?",(remaining,remaining,usage['grant_id']))
    new_accounted=accounted+consumed
    if usage['grant_id'] and consumed:
        now=int(time.time())
        c.execute("INSERT INTO grant_consumption(consumption_id,grant_id,lease_id,session_id,seconds,idempotency_key,recorded_at,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(lease_id) DO UPDATE SET seconds=excluded.seconds,updated_at=excluded.updated_at",('usage:'+lease['lease_id'],usage['grant_id'],lease['lease_id'],usage['session_id'],new_accounted,'usage:'+lease['lease_id'],now,now))
    c.execute('UPDATE usage_sessions SET duration_seconds=?,accounted_seconds=? WHERE lease_id=?',(new_accounted,new_accounted,lease['lease_id']))
    return new_accounted,exhausted

def finalize_lease(c,lease,finished_at,reason):
    duration,exhausted=account_usage(c,lease,finished_at)
    actual_reason='quota_exhausted' if exhausted and reason not in ('operator_blocked','operator_expired') else reason
    actual_finished=int(lease['started_at'])+duration if exhausted else int(finished_at)
    c.execute('UPDATE leases SET finished_at=?,finish_reason=?,duration_seconds=? WHERE lease_id=? AND finished_at IS NULL',(actual_finished,actual_reason,duration,lease['lease_id']))
    c.execute('UPDATE usage_sessions SET last_heartbeat_at=?,ended_at=?,duration_seconds=?,accounted_seconds=?,close_reason=? WHERE lease_id=?',(min(int(finished_at),actual_finished),actual_finished,duration,duration,actual_reason,lease['lease_id']))
    return {'duration_seconds':duration,'finished_at':actual_finished,'finish_reason':actual_reason}

def finish_stale(c,now):
    rows=c.execute('SELECT * FROM leases WHERE finished_at IS NULL AND last_heartbeat+?<=?',(LEASE_GRACE_SECONDS,now)).fetchall()
    for row in rows:
        finalize_lease(c,row,int(row['last_heartbeat'])+LEASE_GRACE_SECONDS,'heartbeat_lost')

def verified_ticket(k,ticket):
    try:
        payload_text,signature_text=ticket.split('.')
        payload=b64ud(payload_text); signature=b64ud(signature_text)
        k.public_key().verify(signature,payload)
        claims=json.loads(payload)
    except Exception:
        return None,'invalid_ticket'
    now=int(time.time())
    required=('operator_id','target_id','session_id','connection_type','grant_id','grant_source','jti','iat','exp')
    if any(not claims.get(f) for f in required) or claims.get('iss')!='masha-auth' or claims.get('v')!=1:
        return None,'invalid_ticket'
    if int(claims['exp'])<=now: return None,'ticket_expired'
    return claims,None

def lease_start(k,req):
    claims,reason=verified_ticket(k,req.get('ticket',''))
    if reason: return False,reason,{}
    now=int(time.time())
    with dbc() as c:
        c.execute('BEGIN IMMEDIATE')
        finish_stale(c,now)
        reason=operator_reason(c,claims['operator_id'],now)
        if reason: return False,reason,{}
        grant=ticket_grant(c,claims,now)
        if not grant: return False,'grant_inactive',{}
        existing=c.execute('SELECT lease_id FROM leases WHERE jti=?',(claims['jti'],)).fetchone()
        if existing: return False,'ticket_replayed',{}
        existing=c.execute('SELECT lease_id FROM usage_sessions WHERE operator_id=? AND session_id=?',(claims['operator_id'],claims['session_id'])).fetchone()
        if existing: return False,'session_replayed',{}
        policy=policy_for_operator(c,claims['operator_id'],now)
        try: max_sessions=int(policy['max_concurrent_sessions']) if policy else int(setting(c,'max_concurrent_sessions','1'))
        except ValueError: max_sessions=1
        max_sessions=max(1,min(max_sessions,100))
        active_count=c.execute('SELECT count(*) FROM leases WHERE operator_id=? AND finished_at IS NULL',(claims['operator_id'],)).fetchone()[0]
        if active_count>=max_sessions: return False,'concurrent_session_limit',{}
        lease_id=secrets.token_urlsafe(18); token=secrets.token_urlsafe(32)
        c.execute('INSERT INTO leases(lease_id,jti,token_hash,operator_id,target_id,session_id,connection_type,started_at,last_heartbeat,grant_id,grant_source) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(lease_id,claims['jti'],token_hash(token),claims['operator_id'],claims['target_id'],claims['session_id'],claims['connection_type'],now,now,claims['grant_id'],claims['grant_source']))
        c.execute('INSERT INTO usage_sessions(usage_id,lease_id,session_id,operator_id,target_id,ticket_jti,grant_id,started_at,last_heartbeat_at) VALUES(?,?,?,?,?,?,?,?,?)',(lease_id,lease_id,claims['session_id'],claims['operator_id'],claims['target_id'],claims['jti'],claims['grant_id'],now,now))
    return True,'allowed',{'lease_id':lease_id,'lease_token':token,'heartbeat_interval_seconds':LEASE_HEARTBEAT_SECONDS,'grace_seconds':LEASE_GRACE_SECONDS,'started_at':now}

def lease_action(req,finish=False):
    lease_id=req.get('lease_id',''); token=req.get('lease_token','')
    if not isinstance(lease_id,str) or not isinstance(token,str) or not lease_id or not token:
        return False,'invalid_request',{}
    now=int(time.time())
    with dbc() as c:
        c.execute('BEGIN IMMEDIATE')
        finish_stale(c,now)
        row=c.execute('SELECT * FROM leases WHERE lease_id=?',(lease_id,)).fetchone()
        if not row or not secrets.compare_digest(row['token_hash'],token_hash(token)):
            return False,'lease_unknown',{}
        if row['finished_at'] is not None:
            data={'duration_seconds':row['duration_seconds'],'finished_at':row['finished_at'],'finish_reason':row['finish_reason']}
            return (True,'finished',data) if finish else (False,row['finish_reason'] or 'lease_finished',data)
        if finish:
            reason=req.get('reason','client_finish')
            if not isinstance(reason,str) or not reason or len(reason)>128: reason='client_finish'
            data=finalize_lease(c,row,now,reason)
            return True,'finished',data
        reason=operator_reason(c,row['operator_id'],now)
        if reason:
            data=finalize_lease(c,row,now,reason)
            return False,data['finish_reason'],data
        duration,exhausted=account_usage(c,row,now)
        if exhausted:
            data=finalize_lease(c,row,now,'quota_exhausted')
            return False,'quota_exhausted',data
        c.execute('UPDATE leases SET last_heartbeat=? WHERE lease_id=?',(now,lease_id))
        c.execute('UPDATE usage_sessions SET last_heartbeat_at=? WHERE lease_id=?',(now,lease_id))
    return True,'allowed',{'server_time':now,'duration_seconds':duration,'grace_seconds':LEASE_GRACE_SECONDS}

class Handler(BaseHTTPRequestHandler):
    server_version='MashaAuth/2'; sys_version=''
    def log_message(self,fmt,*args): LOG.info('%s %s',self.client_address[0],fmt%args)
    def sendj(self,code,obj):
        b=json.dumps(obj,separators=(',',':'),ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','no-store')
        self.end_headers(); self.wfile.write(b)
    def send_html(self,code,html):
        b=html.encode('utf-8')
        self.send_response(code); self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','no-store')
        self.end_headers(); self.wfile.write(b)
    def send_download(self,parsed,send_body=True):
        name=parsed.path.removeprefix('/downloads/')
        digest=DOWNLOAD_FILES.get(name)
        path=DOWNLOAD_ROOT/name
        if not digest or not path.is_file():
            return self.sendj(404,{'error':'not_found'})
        self.send_response(200)
        self.send_header('Content-Type','application/zip')
        self.send_header('Content-Length',str(path.stat().st_size))
        self.send_header('Content-Disposition',f'attachment; filename="{name}"')
        self.send_header('Cache-Control','public, max-age=3600')
        self.send_header('ETag',f'"sha256:{digest}"')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        if send_body:
            with path.open('rb') as source:
                while chunk:=source.read(1024*1024):
                    self.wfile.write(chunk)
    def do_HEAD(self):
        parsed=urlparse(self.path)
        if parsed.path.startswith('/downloads/'):
            return self.send_download(parsed,False)
        self.sendj(404,{'error':'not_found'})

    def do_GET(self):
        parsed=urlparse(self.path)
        if parsed.path=='/health':
            self.sendj(200,{'status':'ok','service':'masha-auth','version':3,'payments_ready':yookassa_ready()})
        elif parsed.path=='/v1/access/status':
            operator_id=parse_qs(parsed.query).get('operator_id',[''])[0].strip()
            if not operator_id or len(operator_id)>256:
                return self.sendj(400,{'allowed':False,'reason':'invalid_request'})
            self.sendj(200,access_status(operator_id))
        elif parsed.path=='/v1/payments/status':
            payment_order_id=parse_qs(parsed.query).get('payment_order_id',[''])[0].strip()
            try: self.sendj(200,payment_order_status(payment_order_id))
            except PaymentError as exc: self.sendj(exc.http_status,{'ok':False,'reason':exc.reason})
        elif parsed.path=='/v1/payments/return':
            self.send_html(200,'<!doctype html><meta charset="utf-8"><title>???? ? ??????</title><body style="font-family:system-ui;padding:32px"><h2>?????? ??????????????</h2><p>??????? ? ?????????? ????. ?????? ??????? ????????? ????????????? ????? ????????????? ?Kassa.</p><p>??? ???? ????? ???????.</p></body>')
        elif parsed.path.startswith('/downloads/'):
            self.send_download(parsed)
        else:
            self.sendj(404,{'error':'not_found'})
    def do_POST(self):
        parsed=urlparse(self.path); path=parsed.path
        paths=('/v1/session/authorize','/v1/session/lease/start','/v1/session/lease/heartbeat','/v1/session/lease/finish','/v1/payments/create','/v1/payments/sync','/v1/webhooks/yookassa')
        if path not in paths: return self.sendj(404,{'error':'not_found'})
        try: n=int(self.headers.get('Content-Length','0'))
        except ValueError: n=0
        if n<=0 or n>MAX_BODY: return self.sendj(400,{'allowed':False,'reason':'invalid_request'})
        try: body=json.loads(self.rfile.read(n).decode())
        except Exception: return self.sendj(400,{'allowed':False,'reason':'invalid_json'})
        if not isinstance(body,dict): return self.sendj(400,{'allowed':False,'reason':'invalid_request'})
        if path=='/v1/payments/create':
            try:
                result=create_yookassa_payment(body)
                LOG.info('payment create operator=%r order=%r status=%s',str(body.get('operator_id',''))[:64],result.get('payment_order_id'),result.get('status'))
                return self.sendj(200,{'ok':True,**result})
            except PaymentError as exc:
                LOG.warning('payment create failed operator=%r reason=%s',str(body.get('operator_id',''))[:64],exc.reason)
                return self.sendj(exc.http_status,{'ok':False,'reason':exc.reason})
        if path=='/v1/payments/sync':
            try: return self.sendj(200,{'ok':True,**sync_yookassa_payment(body)})
            except PaymentError as exc: return self.sendj(exc.http_status,{'ok':False,'reason':exc.reason})
        if path=='/v1/webhooks/yookassa':
            try:
                result=process_yookassa_webhook(body)
                LOG.info('yookassa webhook event=%r payment=%r result=%r',str(body.get('event',''))[:64],str((body.get('object') or {}).get('id',''))[:64],result.get('result',{}).get('reason'))
                return self.sendj(200,{'ok':True})
            except PaymentError as exc:
                LOG.warning('yookassa webhook failed reason=%s retryable=%s',exc.reason,exc.retryable)
                return self.sendj(503 if exc.retryable else exc.http_status,{'ok':False,'reason':exc.reason})
        if path=='/v1/session/authorize':
            ok,reason,ticket,exp=authorize(self.server.signing_key,body)
            LOG.info('authorize operator=%r target=%r allowed=%s reason=%s',str(body.get('operator_id',''))[:64],str(body.get('target_id',''))[:64],ok,reason)
            if not ok: return self.sendj(403,{'allowed':False,'reason':reason})
            return self.sendj(200,{'allowed':True,'ticket':ticket,'expires_at':exp,'ticket_version':1})
        if path.endswith('/start'):
            ok,reason,data=lease_start(self.server.signing_key,body)
        elif path.endswith('/heartbeat'):
            ok,reason,data=lease_action(body)
        else:
            ok,reason,data=lease_action(body,finish=True)
        LOG.info('lease path=%s id=%r allowed=%s reason=%s',path,str(body.get('lease_id',''))[:32],ok,reason)
        response={'allowed':ok,'reason':reason}; response.update(data)
        self.sendj(200 if ok else 403,response)

def serve():
    init_db(); k=ensure_key()
    host=os.getenv('MASHA_AUTH_HOST','127.0.0.1'); port=int(os.getenv('MASHA_AUTH_PORT','18080'))
    h=ThreadingHTTPServer((host,port),Handler); h.signing_key=k
    cert=os.getenv('MASHA_AUTH_TLS_CERT',''); key=os.getenv('MASHA_AUTH_TLS_KEY',''); scheme='http'
    if cert and key:
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.minimum_version=ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert,key); h.socket=ctx.wrap_socket(h.socket,server_side=True); scheme='https'
    LOG.info('listening on %s://%s:%s',scheme,host,port); h.serve_forever()

def parse_valid_until(value):
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        try:
            parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError as exc:
            raise SystemExit('valid-until must be Unix seconds or ISO-8601') from exc
        if parsed.tzinfo is None:
            parsed=parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())

def admin(a):
    init_db(); ensure_key()
    if a.action=='status':
        with dbc() as c:
            print('new_sessions_enabled='+setting(c,'new_sessions_enabled','true'))
            print('ticket_ttl_seconds='+setting(c,'ticket_ttl_seconds',str(DEFAULT_TTL)))
            print('max_concurrent_sessions='+setting(c,'max_concurrent_sessions','1'))
            for r in c.execute('SELECT operator_id,access_status,note,updated_at,valid_until FROM operators ORDER BY operator_id'):
                valid_until='' if r['valid_until'] is None else r['valid_until']
                print(f"operator\t{r['operator_id']}\t{r['access_status']}\t{r['note']}\t{r['updated_at']}\t{valid_until}")
            for r in c.execute('SELECT grant_id,operator_id,source_type,grant_kind,quota_seconds,expires_at,status FROM access_grants ORDER BY operator_id,created_at'):
                print(f"grant\t{r['grant_id']}\t{r['operator_id']}\t{r['source_type']}\t{r['grant_kind']}\t{r['quota_seconds']}\t{r['expires_at']}\t{r['status']}")
            for r in c.execute('SELECT policy_id,mode,rate_minor_per_hour,currency,max_concurrent_sessions,payment_due_seconds,grace_seconds,warning_seconds FROM access_policies ORDER BY policy_id'):
                print(f"policy\t{r['policy_id']}\t{r['mode']}\t{r['rate_minor_per_hour']}\t{r['currency']}\t{r['max_concurrent_sessions']}\t{r['payment_due_seconds']}\t{r['grace_seconds']}\t{r['warning_seconds']}")
            for r in c.execute('SELECT operator_id,policy_id,valid_from,valid_until FROM operator_access_policy ORDER BY operator_id'):
                print(f"operator_policy\t{r['operator_id']}\t{r['policy_id']}\t{r['valid_from']}\t{r['valid_until']}")
            for r in c.execute('SELECT operator_id,billing_status,amount_due_minor,currency,billable_seconds,due_at,grace_until,blocked_at,updated_at FROM billing_accounts ORDER BY operator_id'):
                print(f"billing\t{r['operator_id']}\t{r['billing_status']}\t{r['amount_due_minor']}\t{r['currency']}\t{r['billable_seconds']}\t{r['due_at']}\t{r['grace_until']}\t{r['blocked_at']}\t{r['updated_at']}")
        print('public_key_b64='+PUB64.read_text().strip())
    elif a.action in ('allow','block','expire'):
        if not a.value: raise SystemExit('operator id required')
        if a.action=='block':
            set_operator(a.value,'blocked',a.note)
        elif a.action=='expire':
            set_operator(a.value,'active',a.note,int(time.time())-1)
        else:
            set_operator(a.value,'active',a.note,parse_valid_until(a.valid_until))
        print('ok')
    elif a.action=='remove':
        if not a.value: raise SystemExit('operator id required')
        with dbc() as c:
            c.execute("UPDATE access_grants SET status='revoked' WHERE operator_id=? AND status='active'",(a.value,))
            c.execute('DELETE FROM billing_accounts WHERE operator_id=?',(a.value,))
            c.execute('DELETE FROM operator_access_policy WHERE operator_id=?',(a.value,))
            c.execute('DELETE FROM operators WHERE operator_id=?',(a.value,))
        print('ok')
    elif a.action=='grant':
        if not a.value: raise SystemExit('operator id required')
        if not a.source: raise SystemExit('--source required')
        expires_at=parse_valid_until(a.expires_at)
        grant_id=set_grant(a.value,a.source,a.grant_kind,a.quota_seconds,expires_at,a.source_id,a.priority)
        print('grant_id='+grant_id)
    elif a.action=='revoke-grant':
        if not a.value: raise SystemExit('grant id required')
        with dbc() as c:
            changed=c.execute("UPDATE access_grants SET status='revoked' WHERE grant_id=? AND status='active'",(a.value,)).rowcount
        print('ok' if changed else 'not_active')
    elif a.action=='billing':
        if not a.value: raise SystemExit('operator id required')
        if not a.billing_status: raise SystemExit('--billing-status required')
        set_billing(a.value,a.billing_status); print('ok')
    elif a.action=='postpaid':
        if not a.value: raise SystemExit('operator id required')
        print('grant_id='+enable_postpaid(a.value,a.policy_id))
    elif a.action=='settle':
        if not a.value: raise SystemExit('operator id required')
        print('ok' if settle_postpaid(a.value) else 'not_found')
    elif a.action=='access-status':
        if not a.value: raise SystemExit('operator id required')
        print(json.dumps(access_status(a.value),ensure_ascii=False,sort_keys=True))
    elif a.action=='policy':
        policy_id=a.value or DEFAULT_POSTPAID_POLICY
        set_postpaid_policy(policy_id,a.rate_minor_per_hour,a.payment_due_seconds,a.grace_seconds,a.warning_seconds,a.max_sessions)
        print('ok')
    elif a.action=='usage':
        with dbc() as c:
            if a.value:
                rows=c.execute('SELECT * FROM usage_sessions WHERE operator_id=? ORDER BY started_at',(a.value,))
            else:
                rows=c.execute('SELECT * FROM usage_sessions ORDER BY started_at')
            for r in rows:
                print(f"usage\t{r['usage_id']}\t{r['operator_id']}\t{r['session_id']}\t{r['grant_id']}\t{r['started_at']}\t{r['ended_at']}\t{r['duration_seconds']}\t{r['close_reason']}")
            query='SELECT * FROM grant_consumption WHERE lease_id IN (SELECT lease_id FROM usage_sessions WHERE operator_id=?) ORDER BY recorded_at' if a.value else 'SELECT * FROM grant_consumption ORDER BY recorded_at'
            consumptions=c.execute(query,(a.value,)) if a.value else c.execute(query)
            for r in consumptions:
                print(f"consumption\t{r['consumption_id']}\t{r['grant_id']}\t{r['session_id']}\t{r['seconds']}\t{r['idempotency_key']}")
    elif a.action=='global':
        if a.value not in ('on','off'): raise SystemExit('value must be on/off')
        set_setting('new_sessions_enabled','true' if a.value=='on' else 'false'); print('ok')
    elif a.action=='ttl':
        n=int(a.value)
        if n<30 or n>600: raise SystemExit('ttl must be 30..600')
        set_setting('ticket_ttl_seconds',str(n)); print('ok')
    elif a.action=='concurrency':
        n=int(a.value)
        if n<1 or n>100: raise SystemExit('concurrency must be 1..100')
        set_setting('max_concurrent_sessions',str(n)); print('ok')

def main():
    p=argparse.ArgumentParser(); sp=p.add_subparsers(dest='cmd',required=True); sp.add_parser('serve')
    ap=sp.add_parser('admin'); ap.add_argument('action',choices=['status','allow','block','expire','remove','grant','revoke-grant','billing','postpaid','settle','access-status','policy','usage','global','ttl','concurrency']); ap.add_argument('value',nargs='?'); ap.add_argument('--note',default=''); ap.add_argument('--valid-until'); ap.add_argument('--source',choices=['payment','ad_reward','trial','promo','admin']); ap.add_argument('--grant-kind',choices=['time_credit','unlimited_period'],default='unlimited_period'); ap.add_argument('--quota-seconds',type=int); ap.add_argument('--expires-at'); ap.add_argument('--source-id'); ap.add_argument('--priority',type=int,default=100); ap.add_argument('--billing-status',choices=['current','payment_due','overdue','blocked']); ap.add_argument('--policy-id',default=DEFAULT_POSTPAID_POLICY); ap.add_argument('--rate-minor-per-hour',type=int,default=POSTPAID_RATE_MINOR_PER_HOUR); ap.add_argument('--payment-due-seconds',type=int,default=POSTPAID_DUE_SECONDS); ap.add_argument('--grace-seconds',type=int,default=POSTPAID_GRACE_SECONDS); ap.add_argument('--warning-seconds',type=int,default=POSTPAID_WARNING_SECONDS); ap.add_argument('--max-sessions',type=int,default=1)
    a=p.parse_args(); serve() if a.cmd=='serve' else admin(a)

if __name__=='__main__': main()

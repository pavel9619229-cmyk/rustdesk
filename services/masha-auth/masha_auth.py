#!/usr/bin/env python3
import argparse, base64, hashlib, json, logging, os, secrets, sqlite3, ssl, time
from contextlib import contextmanager
from datetime import datetime, timezone
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
            self.sendj(200,{'status':'ok','service':'masha-auth','version':2})
        elif parsed.path=='/v1/access/status':
            operator_id=parse_qs(parsed.query).get('operator_id',[''])[0].strip()
            if not operator_id or len(operator_id)>256:
                return self.sendj(400,{'allowed':False,'reason':'invalid_request'})
            self.sendj(200,access_status(operator_id))
        elif parsed.path.startswith('/downloads/'):
            self.send_download(parsed)
        else:
            self.sendj(404,{'error':'not_found'})
    def do_POST(self):
        paths=('/v1/session/authorize','/v1/session/lease/start','/v1/session/lease/heartbeat','/v1/session/lease/finish')
        if self.path not in paths: return self.sendj(404,{'error':'not_found'})
        try: n=int(self.headers.get('Content-Length','0'))
        except ValueError: n=0
        if n<=0 or n>MAX_BODY: return self.sendj(400,{'allowed':False,'reason':'invalid_request'})
        try: body=json.loads(self.rfile.read(n).decode())
        except Exception: return self.sendj(400,{'allowed':False,'reason':'invalid_json'})
        if not isinstance(body,dict): return self.sendj(400,{'allowed':False,'reason':'invalid_request'})
        if self.path=='/v1/session/authorize':
            ok,reason,ticket,exp=authorize(self.server.signing_key,body)
            LOG.info('authorize operator=%r target=%r allowed=%s reason=%s',str(body.get('operator_id',''))[:64],str(body.get('target_id',''))[:64],ok,reason)
            if not ok: return self.sendj(403,{'allowed':False,'reason':reason})
            return self.sendj(200,{'allowed':True,'ticket':ticket,'expires_at':exp,'ticket_version':1})
        if self.path.endswith('/start'):
            ok,reason,data=lease_start(self.server.signing_key,body)
        elif self.path.endswith('/heartbeat'):
            ok,reason,data=lease_action(body)
        else:
            ok,reason,data=lease_action(body,finish=True)
        LOG.info('lease path=%s id=%r allowed=%s reason=%s',self.path,str(body.get('lease_id',''))[:32],ok,reason)
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

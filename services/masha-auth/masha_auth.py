#!/usr/bin/env python3
import argparse, base64, hashlib, json, logging, os, secrets, sqlite3, ssl, time
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

BASE=Path(os.getenv('MASHA_AUTH_DIR','/opt/masha-auth'))
DATA=BASE/'data'; SECRETS=BASE/'secrets'
DB=Path(os.getenv('MASHA_AUTH_DB',str(DATA/'auth.db')))
KEY=Path(os.getenv('MASHA_AUTH_SIGNING_KEY',str(SECRETS/'signing_key.pem')))
PUB64=BASE/'public_key.b64'; PUBPEM=BASE/'public_key.pem'
DEFAULT_TTL=120; MAX_BODY=16384
LEASE_HEARTBEAT_SECONDS=10; LEASE_GRACE_SECONDS=30
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

def init_db():
    with dbc() as c:
        c.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        c.execute("CREATE TABLE IF NOT EXISTS operators(operator_id TEXT PRIMARY KEY,access_status TEXT NOT NULL CHECK(access_status IN ('active','blocked')),note TEXT NOT NULL DEFAULT '',updated_at INTEGER NOT NULL)")
        columns={r['name'] for r in c.execute('PRAGMA table_info(operators)')}
        if 'valid_until' not in columns:
            c.execute('ALTER TABLE operators ADD COLUMN valid_until INTEGER')
        c.execute("INSERT OR IGNORE INTO settings VALUES('new_sessions_enabled','true')")
        c.execute("INSERT OR IGNORE INTO settings VALUES('ticket_ttl_seconds',?)",(str(DEFAULT_TTL),))
        c.execute("CREATE TABLE IF NOT EXISTS leases(lease_id TEXT PRIMARY KEY,jti TEXT NOT NULL UNIQUE,token_hash TEXT NOT NULL,operator_id TEXT NOT NULL,target_id TEXT NOT NULL,session_id TEXT NOT NULL,connection_type TEXT NOT NULL,started_at INTEGER NOT NULL,last_heartbeat INTEGER NOT NULL,finished_at INTEGER,finish_reason TEXT NOT NULL DEFAULT '',duration_seconds INTEGER)")

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
    with dbc() as c:
        c.execute('INSERT INTO operators(operator_id,access_status,note,updated_at,valid_until) VALUES(?,?,?,?,?) ON CONFLICT(operator_id) DO UPDATE SET access_status=excluded.access_status,note=excluded.note,updated_at=excluded.updated_at,valid_until=excluded.valid_until',(op,status,note,int(time.time()),valid_until))

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
        row=c.execute('SELECT access_status,valid_until FROM operators WHERE operator_id=?',(op,)).fetchone()
        if not row:
            return False,'operator_unknown',None,None
        if row['access_status']=='blocked':
            return False,'operator_blocked',None,None
        if row['access_status']!='active':
            return False,'operator_inactive',None,None
        if row['valid_until'] is not None and int(row['valid_until'])<=now:
            return False,'operator_expired',None,None
        try: ttl=int(setting(c,'ticket_ttl_seconds',str(DEFAULT_TTL)))
        except ValueError: ttl=DEFAULT_TTL
    ttl=max(30,min(ttl,600)); exp=now+ttl
    claims={'v':1,'iss':'masha-auth','operator_id':op,'target_id':target,'session_id':session,'connection_type':ctype,'client_version':ver,'iat':now,'exp':exp,'jti':secrets.token_urlsafe(18)}
    if nonce:
        claims['target_nonce']=nonce
    payload=json.dumps(claims,sort_keys=True,separators=(',',':')).encode()
    ticket=b64u(payload)+'.'+b64u(k.sign(payload))
    return True,'allowed',ticket,exp

def b64ud(value):
    return base64.urlsafe_b64decode(value+'='*(-len(value)%4))

def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()

def operator_reason(c,operator_id,now):
    row=c.execute('SELECT access_status,valid_until FROM operators WHERE operator_id=?',(operator_id,)).fetchone()
    if not row: return 'operator_unknown'
    if row['access_status']=='blocked': return 'operator_blocked'
    if row['access_status']!='active': return 'operator_inactive'
    if row['valid_until'] is not None and int(row['valid_until'])<=now: return 'operator_expired'
    return None

def finish_stale(c,now):
    c.execute("UPDATE leases SET finished_at=last_heartbeat+?,finish_reason='heartbeat_lost',duration_seconds=(last_heartbeat+?)-started_at WHERE finished_at IS NULL AND last_heartbeat+?<=?",(LEASE_GRACE_SECONDS,LEASE_GRACE_SECONDS,LEASE_GRACE_SECONDS,now))

def verified_ticket(k,ticket):
    try:
        payload_text,signature_text=ticket.split('.')
        payload=b64ud(payload_text); signature=b64ud(signature_text)
        k.public_key().verify(signature,payload)
        claims=json.loads(payload)
    except Exception:
        return None,'invalid_ticket'
    now=int(time.time())
    required=('operator_id','target_id','session_id','connection_type','jti','iat','exp')
    if any(not claims.get(f) for f in required) or claims.get('iss')!='masha-auth' or claims.get('v')!=1:
        return None,'invalid_ticket'
    if int(claims['exp'])<=now: return None,'ticket_expired'
    return claims,None

def lease_start(k,req):
    claims,reason=verified_ticket(k,req.get('ticket',''))
    if reason: return False,reason,{}
    now=int(time.time())
    with dbc() as c:
        finish_stale(c,now)
        reason=operator_reason(c,claims['operator_id'],now)
        if reason: return False,reason,{}
        existing=c.execute('SELECT lease_id FROM leases WHERE jti=?',(claims['jti'],)).fetchone()
        if existing: return False,'ticket_replayed',{}
        lease_id=secrets.token_urlsafe(18); token=secrets.token_urlsafe(32)
        c.execute('INSERT INTO leases(lease_id,jti,token_hash,operator_id,target_id,session_id,connection_type,started_at,last_heartbeat) VALUES(?,?,?,?,?,?,?,?,?)',(lease_id,claims['jti'],token_hash(token),claims['operator_id'],claims['target_id'],claims['session_id'],claims['connection_type'],now,now))
    return True,'allowed',{'lease_id':lease_id,'lease_token':token,'heartbeat_interval_seconds':LEASE_HEARTBEAT_SECONDS,'grace_seconds':LEASE_GRACE_SECONDS,'started_at':now}

def lease_action(req,finish=False):
    lease_id=req.get('lease_id',''); token=req.get('lease_token','')
    if not isinstance(lease_id,str) or not isinstance(token,str) or not lease_id or not token:
        return False,'invalid_request',{}
    now=int(time.time())
    with dbc() as c:
        finish_stale(c,now)
        row=c.execute('SELECT * FROM leases WHERE lease_id=?',(lease_id,)).fetchone()
        if not row or not secrets.compare_digest(row['token_hash'],token_hash(token)):
            return False,'lease_unknown',{}
        if row['finished_at'] is not None:
            data={'duration_seconds':row['duration_seconds'],'finished_at':row['finished_at']}
            return (True,'finished',data) if finish else (False,row['finish_reason'] or 'lease_finished',data)
        if finish:
            reason=req.get('reason','client_finish')
            if not isinstance(reason,str) or not reason or len(reason)>128: reason='client_finish'
            duration=max(0,now-row['started_at'])
            c.execute('UPDATE leases SET finished_at=?,finish_reason=?,duration_seconds=? WHERE lease_id=?',(now,reason,duration,lease_id))
            return True,'finished',{'duration_seconds':duration,'finished_at':now}
        reason=operator_reason(c,row['operator_id'],now)
        if reason:
            duration=max(0,now-row['started_at'])
            c.execute('UPDATE leases SET finished_at=?,finish_reason=?,duration_seconds=? WHERE lease_id=?',(now,reason,duration,lease_id))
            return False,reason,{'duration_seconds':duration}
        c.execute('UPDATE leases SET last_heartbeat=? WHERE lease_id=?',(now,lease_id))
    return True,'allowed',{'server_time':now,'grace_seconds':LEASE_GRACE_SECONDS}

class Handler(BaseHTTPRequestHandler):
    server_version='MashaAuth/1'; sys_version=''
    def log_message(self,fmt,*args): LOG.info('%s %s',self.client_address[0],fmt%args)
    def sendj(self,code,obj):
        b=json.dumps(obj,separators=(',',':'),ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','no-store')
        self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path=='/health': self.sendj(200,{'status':'ok','service':'masha-auth','version':1})
        else: self.sendj(404,{'error':'not_found'})
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
            for r in c.execute('SELECT operator_id,access_status,note,updated_at,valid_until FROM operators ORDER BY operator_id'):
                valid_until='' if r['valid_until'] is None else r['valid_until']
                print(f"{r['operator_id']}\t{r['access_status']}\t{r['note']}\t{r['updated_at']}\t{valid_until}")
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
        with dbc() as c: c.execute('DELETE FROM operators WHERE operator_id=?',(a.value,))
        print('ok')
    elif a.action=='global':
        if a.value not in ('on','off'): raise SystemExit('value must be on/off')
        set_setting('new_sessions_enabled','true' if a.value=='on' else 'false'); print('ok')
    elif a.action=='ttl':
        n=int(a.value)
        if n<30 or n>600: raise SystemExit('ttl must be 30..600')
        set_setting('ticket_ttl_seconds',str(n)); print('ok')

def main():
    p=argparse.ArgumentParser(); sp=p.add_subparsers(dest='cmd',required=True); sp.add_parser('serve')
    ap=sp.add_parser('admin'); ap.add_argument('action',choices=['status','allow','block','expire','remove','global','ttl']); ap.add_argument('value',nargs='?'); ap.add_argument('--note',default=''); ap.add_argument('--valid-until')
    a=p.parse_args(); serve() if a.cmd=='serve' else admin(a)

if __name__=='__main__': main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cloud-ready multi-user price tracker starter (Python stdlib only)."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import sqlite3, json, os, secrets, hashlib, hmac, threading, time, urllib.request, urllib.error, urllib.parse, re, socket, ipaddress, smtplib, ssl, html, base64

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('DATABASE_PATH', str(BASE / 'cloud_price_tracker.db')))
HOST = os.environ.get('HOST', '0.0.0.0')
PORT = int(os.environ.get('PORT', '8080'))
CHECK_HOURS = max(1, int(os.environ.get('CHECK_INTERVAL_HOURS', '6')))
APP_SECRET = os.environ.get('APP_SECRET', '')
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', '').strip().lower()
PLAN_LIMITS = {'free': 5, 'pro': 100, 'admin': 1000000}
PRO_PRICE_KRW = max(100, int(os.environ.get('PRO_PRICE_KRW', '3900')))
TOSS_CLIENT_KEY = os.environ.get('TOSS_CLIENT_KEY', '').strip()
TOSS_SECRET_KEY = os.environ.get('TOSS_SECRET_KEY', '').strip()
PUBLIC_BASE_URL = os.environ.get('PUBLIC_BASE_URL', '').strip().rstrip('/')
PAYMENTS_ENABLED = bool(TOSS_CLIENT_KEY and TOSS_SECRET_KEY and PUBLIC_BASE_URL.startswith('https://'))
BILLING_ENABLED = PAYMENTS_ENABLED
BILLING_INTERVAL_DAYS = max(1, int(os.environ.get('BILLING_INTERVAL_DAYS', '30')))
PRO_PLAN_NAME = os.environ.get('PRO_PLAN_NAME', '가격 추적기 PRO 월 구독')


def now(): return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA journal_mode=WAL')
    return c

def init_db():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL, plan TEXT NOT NULL DEFAULT 'free', plan_expires TEXT NOT NULL DEFAULT '', role TEXT NOT NULL DEFAULT 'user');
        CREATE TABLE IF NOT EXISTS subscription_events(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, event TEXT NOT NULL, plan TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS payment_orders(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, order_id TEXT UNIQUE NOT NULL, plan TEXT NOT NULL, amount INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', payment_key TEXT UNIQUE, created_at TEXT NOT NULL, paid_at TEXT NOT NULL DEFAULT '', provider_payload TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, csrf TEXT NOT NULL, expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS billing_customers(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, customer_key TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS subscriptions(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, customer_key TEXT NOT NULL, billing_key_enc TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', amount INTEGER NOT NULL, next_billing_at TEXT NOT NULL, last_payment_key TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_subscriptions_due ON subscriptions(status,next_billing_at);
        CREATE TABLE IF NOT EXISTS auth_attempts(id INTEGER PRIMARY KEY, ip TEXT NOT NULL, action TEXT NOT NULL, succeeded INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_auth_attempts_ip_time ON auth_attempts(ip,action,created_at);
        CREATE TABLE IF NOT EXISTS backup_events(id INTEGER PRIMARY KEY, file_name TEXT NOT NULL, size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS account_tokens(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,token_hash TEXT UNIQUE NOT NULL,purpose TEXT NOT NULL,expires_at TEXT NOT NULL,used_at TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS billing_events(id INTEGER PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS refund_records(id INTEGER PRIMARY KEY, payment_key TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id), amount INTEGER NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, provider_payload TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS system_events(id INTEGER PRIMARY KEY, level TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL, url TEXT NOT NULL, target_price INTEGER, shipping INTEGER NOT NULL DEFAULT 0, current_price INTEGER, last_checked TEXT, last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS price_history(id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE, price INTEGER NOT NULL, checked_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE, message TEXT NOT NULL, created_at TEXT NOT NULL, emailed INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS idx_products_due ON products(last_checked);
        CREATE INDEX IF NOT EXISTS idx_products_user ON products(user_id);
        ''')
        # Backward-compatible v8 -> v9 schema migration.
        cols={r['name'] for r in c.execute('PRAGMA table_info(users)')}
        for name, decl in [('plan', "TEXT NOT NULL DEFAULT 'free'"), ('plan_expires', "TEXT NOT NULL DEFAULT ''"), ('role', "TEXT NOT NULL DEFAULT 'user'")]:
            if name not in cols: c.execute(f'ALTER TABLE users ADD COLUMN {name} {decl}')
        # v12 additive migration; safe for existing v10/v11 databases.
        subcols={r['name'] for r in c.execute('PRAGMA table_info(subscriptions)')}
        if 'retry_count' not in subcols: c.execute('ALTER TABLE subscriptions ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0')
        if 'last_failure' not in subcols: c.execute("ALTER TABLE subscriptions ADD COLUMN last_failure TEXT NOT NULL DEFAULT ''")
        usercols={r['name'] for r in c.execute('PRAGMA table_info(users)')}
        if 'email_verified' not in usercols: c.execute('ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 1')
        if ADMIN_EMAIL:
            c.execute("UPDATE users SET role='admin', plan='admin' WHERE email=?", (ADMIN_EMAIL,))

def pw_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode() + b'\0' + APP_SECRET.encode(), salt, 310000)
    return salt.hex() + '$' + key.hex()

def pw_verify(password, stored):
    try:
        salt_hex, key_hex = stored.split('$', 1)
        return hmac.compare_digest(pw_hash(password, bytes.fromhex(salt_hex)).split('$',1)[1], key_hex)
    except Exception: return False

def public_host(host):
    if not host or host.lower() in ('localhost',) or host.lower().endswith(('.local', '.internal')): raise ValueError('공개 웹사이트 주소만 등록할 수 있습니다.')
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        if not infos: raise ValueError('도메인을 확인할 수 없습니다.')
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global: raise ValueError('내부망 주소는 조회할 수 없습니다.')
    except socket.gaierror: raise ValueError('상품 주소의 도메인을 확인할 수 없습니다.')

def validate_url(url):
    p = urlparse(url)
    if p.scheme not in ('http','https') or not p.hostname: raise ValueError('http 또는 https 상품 주소를 입력해 주세요.')
    public_host(p.hostname)
    return url

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def extract_price(html_text):
    vals=[]
    # JSON-LD Product/Offer data; accept KRW only.
    scripts=re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script\s*>', html_text, re.I|re.S)
    def walk(x):
        if isinstance(x,dict):
            typ=x.get('@type',[]); typ=[typ] if isinstance(typ,str) else typ
            if any(str(t).lower() in ('product','offer','aggregateoffer') for t in typ):
                offers=x.get('offers',x); offers=offers if isinstance(offers,list) else [offers]
                for o in offers:
                    if isinstance(o,dict) and str(o.get('priceCurrency','KRW')).upper() in ('KRW','WON'):
                        for k in ('price','lowPrice'):
                            try:
                                n=int(float(str(o.get(k,'')).replace(',','')))
                                if n>0: vals.append(n)
                            except Exception: pass
            for v in x.values(): walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    for s in scripts:
        try: walk(json.loads(s))
        except Exception: pass
    if vals: return min(vals)
    pats=[r'<meta[^>]+itemprop=["\']price["\'][^>]+content=["\']([0-9,.]+)',r'<meta[^>]+content=["\']([0-9,.]+)["\'][^>]+itemprop=["\']price']
    for pat in pats:
        m=re.search(pat,html_text,re.I)
        if m:
            try:
                n=int(float(m.group(1).replace(',','')))
                if n>0:return n
            except Exception: pass
    raise ValueError('페이지에서 공개 가격을 찾지 못했습니다. 로그인·옵션 선택·자바스크립트가 필요한 상품일 수 있습니다.')

def fetch_price(url):
    validate_url(url)
    opener=urllib.request.build_opener(SafeRedirect())
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 PriceTracker/9.0','Accept':'text/html,application/xhtml+xml'})
    with opener.open(req,timeout=18) as r:
        validate_url(r.geturl())
        if r.status != 200: raise ValueError('상품 페이지 응답 오류')
        raw=r.read(2_000_000)
        charset=r.headers.get_content_charset() or 'utf-8'
    return extract_price(raw.decode(charset,'replace'))

def send_email(to, subject, body):
    host=os.environ.get('SMTP_HOST','').strip()
    if not host: return False
    msg=EmailMessage(); msg['Subject']=subject; msg['From']=os.environ.get('SMTP_FROM',os.environ.get('SMTP_USER',''))
    msg['To']=to; msg.set_content(body)
    port=int(os.environ.get('SMTP_PORT','587')); user=os.environ.get('SMTP_USER',''); pw=os.environ.get('SMTP_PASSWORD','')
    if os.environ.get('SMTP_TLS','true').lower() == 'true':
        with smtplib.SMTP(host,port,timeout=20) as s:
            s.starttls(context=ssl.create_default_context())
            if user:s.login(user,pw)
            s.send_message(msg)
    else:
        with smtplib.SMTP_SSL(host,port,timeout=20,context=ssl.create_default_context()) as s:
            if user:s.login(user,pw)
            s.send_message(msg)
    return True


def toss_api(method, path, payload=None, timeout=65):
    if not TOSS_SECRET_KEY: raise RuntimeError('Toss secret key가 설정되지 않았습니다.')
    data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
    auth=base64.b64encode((TOSS_SECRET_KEY+':').encode()).decode()
    req=urllib.request.Request('https://api.tosspayments.com'+path,data=data,headers={'Authorization':'Basic '+auth,'Content-Type':'application/json'},method=method)
    with urllib.request.urlopen(req,timeout=timeout) as response: return json.loads(response.read(200000).decode('utf-8'))

def encrypt_billing_key(value):
    from cryptography.fernet import Fernet
    key=base64.urlsafe_b64encode(hashlib.sha256((APP_SECRET+'|price-tracker-billing-v1').encode()).digest())
    return Fernet(key).encrypt(value.encode()).decode()

def decrypt_billing_key(value):
    from cryptography.fernet import Fernet
    key=base64.urlsafe_b64encode(hashlib.sha256((APP_SECRET+'|price-tracker-billing-v1').encode()).digest())
    return Fernet(key).decrypt(value.encode()).decode()

def record_system_event(level,event,detail=''):
    try:
        with connect() as c: c.execute('INSERT INTO system_events(level,event,detail,created_at) VALUES(?,?,?,?)',(level,event,str(detail)[:500],now()))
    except Exception: pass

def charge_subscription(sub):
    order_id='ptb_'+secrets.token_urlsafe(24).replace('-','').replace('_','')[:30]
    billing_key=decrypt_billing_key(sub['billing_key_enc'])
    with connect() as c: c.execute("INSERT INTO payment_orders(user_id,order_id,plan,amount,status,created_at) VALUES(?,?,?,?,?,?)",(sub['user_id'],order_id,'pro_monthly',sub['amount'],'pending',now()))
    result=toss_api('POST','/v1/billing/'+urllib.parse.quote(billing_key,safe=''),{'customerKey':sub['customer_key'],'amount':int(sub['amount']),'orderId':order_id,'orderName':PRO_PLAN_NAME,'customerEmail':sub['email']},timeout=65)
    if result.get('status')!='DONE' or result.get('orderId')!=order_id or int(result.get('totalAmount',-1))!=int(sub['amount']): raise ValueError('자동결제 응답 검증 실패')
    paid=now(); payment_key=result.get('paymentKey',''); expires=(datetime.now(timezone.utc)+timedelta(days=BILLING_INTERVAL_DAYS)).strftime('%Y-%m-%d %H:%M:%S UTC')
    with connect() as c:
        c.execute("UPDATE payment_orders SET status='paid',payment_key=?,paid_at=?,provider_payload=? WHERE order_id=? AND status='pending'",(payment_key,paid,json.dumps({'status':result.get('status'),'orderId':order_id,'totalAmount':result.get('totalAmount'),'paymentKey':payment_key},ensure_ascii=False),order_id))
        c.execute("UPDATE subscriptions SET status='active',next_billing_at=?,last_payment_key=?,updated_at=?,retry_count=0,last_failure='' WHERE id=?",(expires,payment_key,paid,sub['id']))
        c.execute("UPDATE users SET plan='pro',plan_expires=? WHERE id=?",(expires,sub['user_id']))
        c.execute("INSERT INTO subscription_events(user_id,event,plan,note,created_at) VALUES(?,?,?,?,?)",(sub['user_id'],'recurring_payment_succeeded','pro',order_id+' / '+str(sub['amount'])+'원',paid))
    return result

def billing_worker():
    while True:
        try:
            with connect() as c: due=c.execute("SELECT s.*,u.email FROM subscriptions s JOIN users u ON u.id=s.user_id WHERE s.status IN ('active','past_due') AND s.next_billing_at<=? ORDER BY s.next_billing_at LIMIT 20",(now(),)).fetchall()
            for sub in due:
                try: charge_subscription(sub)
                except Exception as e:
                    attempts=int(sub['retry_count'] or 0)+1
                    # Retry after 1, 6, then 24 hours. Stop after 4 failures.
                    delays={1:1,2:6,3:24}
                    terminal=attempts>=4
                    next_try=(datetime.now(timezone.utc)+timedelta(hours=delays.get(attempts,24))).strftime('%Y-%m-%d %H:%M:%S UTC')
                    with connect() as c:
                        c.execute("UPDATE subscriptions SET status=?,updated_at=?,retry_count=?,last_failure=?,next_billing_at=? WHERE id=? AND status IN ('active','past_due')",('payment_failed' if terminal else 'past_due',now(),attempts,str(e)[:300],next_try,sub['id']))
                        c.execute("INSERT INTO subscription_events(user_id,event,plan,note,created_at) VALUES(?,?,?,?,?)",(sub['user_id'],'recurring_payment_failed','pro',f'{attempts}회차: {str(e)[:250]}',now()))
                    record_system_event('ERROR','recurring_payment_failed',f"subscription={sub['id']} attempt={attempts}: {e}")
                    if terminal:
                        with connect() as c: c.execute("UPDATE users SET plan='free',plan_expires='' WHERE id=?",(sub['user_id'],))
                        try: send_email(sub['email'],'가격 추적기 구독 결제 실패', '정기결제가 4회 실패하여 PRO 이용이 무료 요금제로 전환되었습니다. 결제수단을 확인한 뒤 다시 구독해 주세요.')
                        except Exception as mail_err: record_system_event('WARN','billing_failure_email_failed',str(mail_err))
        except Exception as e: record_system_event('ERROR','billing_worker_error',str(e))
        time.sleep(60)

def price_worker():
    while True:
        try:
            with connect() as c:
                due=c.execute('''SELECT p.*,u.email FROM products p JOIN users u ON u.id=p.user_id
                    WHERE p.last_checked IS NULL OR p.last_checked < ? ORDER BY p.last_checked LIMIT 30''',
                    ((datetime.now(timezone.utc)-timedelta(hours=CHECK_HOURS)).strftime('%Y-%m-%d %H:%M:%S UTC'),)).fetchall()
            for p in due:
                try:
                    price=fetch_price(p['url'])
                    with connect() as c:
                        c.execute('INSERT INTO price_history(product_id,price,checked_at) VALUES(?,?,?)',(p['id'],price,now()))
                        c.execute('UPDATE products SET current_price=?,last_checked=?,last_error=? WHERE id=?',(price,now(),'',p['id']))
                        total=price+int(p['shipping'] or 0)
                        crossed = (p['current_price'] is None or int(p['current_price']) + int(p['shipping'] or 0) > int(p['target_price'] or 0))
                        if p['target_price'] and total<=p['target_price'] and crossed:
                            msg=f"{p['name']} 목표가 도달: 배송 포함 {total:,}원 (목표 {p['target_price']:,}원)"
                            c.execute('INSERT INTO alerts(user_id,product_id,message,created_at) VALUES(?,?,?,?)',(p['user_id'],p['id'],msg,now()))
                    # Send unsent alerts for this account; SMTP is optional.
                    with connect() as c:
                        pending=c.execute('SELECT id,message FROM alerts WHERE user_id=? AND emailed=0 ORDER BY id',(p['user_id'],)).fetchall()
                    for a in pending:
                        try:
                            if send_email(p['email'],'가격 추적기 목표가 알림',a['message']):
                                with connect() as c:c.execute('UPDATE alerts SET emailed=1 WHERE id=?',(a['id'],))
                        except Exception as mail_err: print('Email error:',mail_err,flush=True)
                except Exception as e:
                    with connect() as c:c.execute('UPDATE products SET last_checked=?,last_error=? WHERE id=?',(now(),str(e)[:300],p['id']))
                    record_system_event('WARN','price_check_failed',f"product={p['id']} {e}")
        except Exception as e:
            record_system_event('ERROR','price_worker_error',str(e))
            print('Worker error:',e,flush=True)
        time.sleep(300)

PAGE='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#2458d3"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="default"><meta name="apple-mobile-web-app-title" content="가격 추적기"><link rel="manifest" href="/manifest.webmanifest"><link rel="icon" href="/icons/icon-192.png"><link rel="apple-touch-icon" href="/icons/icon-192.png"><title>가격 추적기 클라우드</title><style>
*{box-sizing:border-box}body{font-family:Inter,system-ui,-apple-system,sans-serif;background:#f5f7fc;color:#17233d;margin:0}.wrap{max-width:1080px;margin:34px auto;padding:0 20px}.brand{display:flex;align-items:center;gap:13px;margin-bottom:8px}.brandmark{display:grid;place-items:center;width:46px;height:46px;border-radius:15px;background:linear-gradient(135deg,#315fe8,#6b8cff);color:#fff;font-size:23px;font-weight:900;box-shadow:0 8px 20px #315fe833}.brand h1{font-size:27px;letter-spacing:-.8px;margin:0}.brand small{display:block;font-size:12px;color:#71809a;font-weight:600;letter-spacing:1.4px;margin-top:2px}.hero-note{color:#71809a;margin:10px 0 22px;font-size:14px}.panel{background:#fff;border:1px solid #e5eaf3;border-radius:20px;padding:24px;margin:16px 0;box-shadow:0 8px 28px #1d35570a}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:13px}.field label{display:block;font-size:13px;font-weight:700;color:#53627b;margin:2px 0 6px}input{width:100%;padding:13px 14px;border:1px solid #dce3ef;border-radius:11px;margin:0 0 12px;font:inherit;outline:none;background:#fbfcff}input:focus{border-color:#6d8df0;box-shadow:0 0 0 3px #315fe81a}button{background:#315fe8;color:white;border:0;border-radius:11px;padding:12px 17px;cursor:pointer;font-weight:750;transition:transform .15s,background .15s}button:hover{background:#244dcc;transform:translateY(-1px)}button:focus-visible{outline:3px solid #9bb2ff;outline-offset:2px}.secondary{background:#edf2ff;color:#2852c4}.secondary:hover{background:#dfe8ff}.search-row{display:flex;gap:8px}.search-row input{min-width:0;flex:1}.search-row button{white-space:nowrap;padding:11px 13px}.muted{color:#71809a;font-size:13px;line-height:1.6}.product{border-top:1px solid #edf0f6;padding:17px 0}.product b{font-size:15px}.error{color:#b42318}.ok{color:#087443}a{word-break:break-all;color:#315fe8}.section-title{font-size:19px;letter-spacing:-.4px}.hint{background:#f2f6ff;border:1px solid #e2eaff;border-radius:12px;padding:12px 14px;margin:4px 0 16px;color:#526789;font-size:13px;line-height:1.6}.primary-wide{margin-top:3px;min-width:150px}@media(max-width:600px){.wrap{margin:20px auto;padding:0 14px}.panel{padding:18px;border-radius:16px}.brand h1{font-size:23px}.search-row{flex-direction:column}.search-row button{width:100%}}</style>
<div class="wrap"><header class="brand"><div class="brandmark">P</div><div><h1>PriceNavi</h1><small>SMART PRICE TRACKER</small></div></header><p class="hero-note">원하는 상품의 가격을 추적하고, 목표 가격에 도달하면 알려드려요. <button id="installApp" class="secondary" hidden>앱 설치</button> <span id="installHelp" class="muted">휴대폰·PC에 앱으로 설치할 수 있습니다.</span></p>
<section class="panel" id="auth"><h2>회원가입 / 로그인</h2><div class="grid"><div><label>이메일</label><input id="email" type="email" autocomplete="email"></div><div><label>비밀번호 (8자 이상)</label><input id="password" type="password" autocomplete="current-password"></div></div><button onclick="auth('register')">회원가입</button> <button class="secondary" onclick="auth('login')">로그인</button><p id="authmsg" class="muted"></p></section>
<section class="panel" id="app" hidden><div id="account" class="muted"></div><div id="admin" hidden class="panel"><h3>관리자 · 사용자 및 구독 관리</h3><div id="adminusers"></div><div id="admindash"></div></div><div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><h2>내 추적 상품</h2><button class="secondary" onclick="logout()">로그아웃</button></div><h3 class="section-title">새 상품 가격 추적하기</h3><div class="grid"><div class="field"><label for="name">상품명</label><div class="search-row"><input id="name" placeholder="예: 무선 이어폰, 로봇청소기"><button type="button" class="secondary" onclick="openGoogleShopping()">구글 쇼핑 검색</button></div></div><div class="field"><label for="url">상품 URL</label><input id="url" placeholder="선택한 상품의 주소를 붙여넣으세요"></div><div class="field"><label for="target">목표가 (원)</label><input id="target" type="number" min="1" placeholder="예: 89000"></div><div class="field"><label for="shipping">배송비 (원)</label><input id="shipping" type="number" min="0" value="0"></div></div><div class="hint">① 상품명을 입력하고 구글 쇼핑 검색을 누르세요. ② 원하는 상품을 열어 주소를 복사한 뒤 상품 URL에 붙여넣으세요. ③ 목표가를 입력하고 추적을 등록하면 됩니다.</div><button class="primary-wide" onclick="addProduct()">가격 추적 시작</button><p id="msg" class="muted"></p><div id="products"></div><h3>목표가 알림</h3><div id="alerts" class="muted">알림 없음</div></section>
<p class="muted">가격 확인은 서버가 실행 중일 때 최대 설정 주기마다 시도합니다. 판매처가 자동 조회를 차단하거나 공개 가격 데이터를 제공하지 않으면 확인에 실패할 수 있습니다. 상품 페이지 접근은 판매처의 이용약관 및 robots 정책을 준수해야 합니다.</p></div><script src="https://js.tosspayments.com/v2/standard"></script><script>
let csrf='';let deferredInstall=null;const installBtn=document.getElementById('installApp');window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();deferredInstall=e;installBtn.hidden=false;});installBtn.addEventListener('click',async()=>{if(deferredInstall){deferredInstall.prompt();await deferredInstall.userChoice;deferredInstall=null;installBtn.hidden=true;}else{document.getElementById('installHelp').textContent='iPhone: Safari 공유 버튼 → 홈 화면에 추가 · Android/PC: 브라우저 메뉴 → 앱 설치 또는 홈 화면에 추가';}});if('serviceWorker' in navigator && (location.protocol==='https:'||location.hostname==='localhost')){window.addEventListener('load',()=>navigator.serviceWorker.register('/sw.js').catch(()=>{}));}if(/iphone|ipad|ipod/i.test(navigator.userAgent)&&!window.matchMedia('(display-mode: standalone)').matches){installBtn.hidden=false;installBtn.textContent='설치 방법';}
async function req(path,data={}){let r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)});let j=await r.json();if(!r.ok)throw Error(j.error||'요청 실패');return j}
async function auth(mode){try{let j=await req('/api/'+mode,{email:email.value,password:password.value});csrf=j.csrf;authmsg.textContent='로그인되었습니다.';showApp()}catch(e){authmsg.textContent=e.message}}
async function showApp(){auth.hidden=true;app.hidden=false;await load();await loadAccount()}
async function loadAccount(){try{let r=await fetch('/api/account');let a=await r.json();account.innerHTML=`계정: ${esc(a.email)} · 요금제: <b>${esc(a.plan.toUpperCase())}</b> · 상품 ${a.used}/${a.limit}개 ${a.expires?'· 만료 '+esc(a.expires):''}<br><span class=muted>PRO: 월 30일 이용 · ${Number(a.pro_price).toLocaleString('ko-KR')}원</span> ${a.payments_enabled?'<button onclick=buyPro()>PRO 30일 이용권</button> <button class=secondary onclick=startRecurring()>월 자동결제 등록</button> <button class=secondary onclick=cancelRecurring()>자동갱신 해지</button>':'<span class=muted> · 결제 설정 대기 중</span>'}`;if(a.role==='admin'){admin.hidden=false;let q=await fetch('/api/admin/users');let j=await q.json();adminusers.innerHTML=j.users.map(u=>`<div class="product">#${u.id} ${esc(u.email)} · ${esc(u.plan)} · 상품 ${u.product_count}개 <select id="plan${u.id}"><option value="free">무료</option><option value="pro" ${u.plan==='pro'?'selected':''}>PRO 30일</option></select> <button class="secondary" onclick="setPlan(${u.id})">변경</button></div>`).join('');let z=await fetch('/api/admin/overview');if(z.ok){let v=await z.json();admindash.innerHTML=`<h3>운영 현황</h3><div class=grid><div>회원 ${v.users}</div><div>유료회원 ${v.pro_users}</div><div>누적 결제 ${Number(v.paid_total).toLocaleString()}원</div><div>이번 달 결제 ${Number(v.month_total).toLocaleString()}원</div><div>자동구독 ${v.active_subscriptions}</div><div>결제 실패 ${v.failed_payments}</div><div>24시간 오류 ${v.errors_24h}</div></div><h3>최근 결제</h3>${v.payments.map(x=>`<p>${esc(x.email)} · ${Number(x.amount).toLocaleString()}원 · ${esc(x.status)} · ${esc(x.created_at)} ${x.payment_key?`<button class=secondary onclick="refundPayment('${esc(x.payment_key)}')">환불</button>`:''}</p>`).join('')}<h3>최근 시스템 이벤트</h3>${v.events.map(e=>`<p>${esc(e.created_at)} · ${esc(e.level)} · ${esc(e.event)} · ${esc(e.detail)}</p>`).join('')}`}} }catch(e){account.textContent='계정 정보를 불러오지 못했습니다.'}}
async function buyPro(){try{let o=await req('/api/billing/order',{});if(!o.clientKey)throw Error('결제 키 설정이 필요합니다.');let toss=window.TossPayments(o.clientKey);let payment=toss.payment({customerKey:o.customerKey});await payment.requestPayment({method:'CARD',amount:{currency:'KRW',value:o.amount},orderId:o.orderId,orderName:o.orderName,customerEmail:o.customerEmail,successUrl:o.successUrl,failUrl:o.failUrl});}catch(e){if(e.message)alert(e.message)}}
async function setPlan(uid){try{await req('/api/admin/plan',{user_id:uid,plan:document.getElementById('plan'+uid).value,days:30});await loadAccount();alert('요금제 설정을 저장했습니다.')}catch(e){alert(e.message)}}
async function startRecurring(){try{let r=await fetch('/api/billing/setup',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:'{}'});let j=await r.json();if(!r.ok)throw Error(j.error||'요청 실패');let toss=window.TossPayments(j.clientKey);let payment=toss.payment({customerKey:j.customerKey});await payment.requestBillingAuth({method:'CARD',successUrl:j.successUrl,failUrl:j.failUrl});}catch(e){alert(e.message||'카드 등록을 시작하지 못했습니다.')}}
async function cancelRecurring(){if(!confirm('자동갱신을 중단할까요? 현재 결제기간은 유지됩니다.'))return;try{await req('/api/billing/cancel',{});alert('자동갱신이 해지되었습니다.');await loadAccount()}catch(e){alert(e.message)}}
async function refundPayment(paymentKey){let reason=prompt('환불 사유를 입력하세요.');if(!reason)return;try{await req('/api/admin/refund',{paymentKey,reason});alert('환불 처리 완료');await loadAccount()}catch(e){alert('환불 실패: '+e.message)}}
async function logout(){await req('/api/logout');location.reload()}
function openGoogleShopping(){const q=document.getElementById('name').value.trim();if(!q){document.getElementById('msg').textContent='먼저 상품명을 입력해 주세요.';document.getElementById('name').focus();return;}window.open('https://www.google.com/search?tbm=shop&q='+encodeURIComponent(q),'_blank','noopener,noreferrer');document.getElementById('msg').textContent='구글 쇼핑에서 상품을 선택한 뒤 상품 페이지 주소를 복사해 붙여넣으세요.'}
async function addProduct(){try{const productName=document.getElementById('name').value.trim();const productUrl=document.getElementById('url').value.trim();const targetPrice=document.getElementById('target').value;const shippingCost=document.getElementById('shipping').value;let j=await req('/api/products/add',{name:productName,url:productUrl,target_price:targetPrice,shipping:shippingCost});msg.textContent='등록 완료. 첫 가격 확인은 백그라운드 작업에서 진행됩니다.';document.getElementById('name').value='';document.getElementById('url').value='';document.getElementById('target').value='';await load();await loadAccount()}catch(e){msg.textContent=e.message}}
async function load(){let r=await fetch('/api/data');if(r.status===401){auth.hidden=false;app.hidden=true;return}let j=await r.json();products.innerHTML=j.products.map(p=>`<div class="product"><b>${esc(p.name)}</b> · ${p.current_price===null?'확인 전':Number(p.current_price).toLocaleString('ko-KR')+'원'} <span class="muted">(+배송 ${Number(p.shipping).toLocaleString('ko-KR')}원)</span><br><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.url)}</a><p class="muted">목표가 ${p.target_price?Number(p.target_price).toLocaleString('ko-KR')+'원':'미설정'} · 최근 확인 ${esc(p.last_checked||'대기 중')} · ${esc(p.last_error||'')}</p><button class="secondary" onclick="checkPrice(${p.id})">지금 가격 확인</button> <button class="secondary" onclick="compareGoogle('${encodeURIComponent(p.name)}')">구글 쇼핑 최저가 검색</button> <button class="secondary" onclick="del(${p.id})">삭제</button></div>`).join('')||'<p class="muted">등록된 상품이 없습니다.</p>';alerts.innerHTML=j.alerts.map(a=>`<p>${esc(a.created_at)} · ${esc(a.message)}</p>`).join('')||'알림 없음'}
async function checkPrice(id){try{msg.textContent='상품 페이지에서 가격을 확인하고 있습니다. 잠시만 기다려 주세요.';await req('/api/products/check',{id});msg.textContent='가격 확인을 완료했습니다.';await load()}catch(e){msg.textContent='가격 확인 실패: '+e.message;await load()}}
function compareGoogle(encodedName){window.open('https://www.google.com/search?tbm=shop&q='+encodedName,'_blank','noopener,noreferrer')}
async function del(id){if(!confirm('이 상품을 삭제할까요?'))return;await req('/api/products/delete',{id});load()}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
(async()=>{let r=await fetch('/api/me');if(r.ok){let j=await r.json();csrf=j.csrf;showApp();let q=new URLSearchParams(location.search);if(location.pathname==='/billing/issue'&&q.has('authKey')){try{await req('/api/billing/issue',{authKey:q.get('authKey'),customerKey:q.get('customerKey')});history.replaceState({},'', '/');await loadAccount();alert('정기결제 등록과 첫 결제가 완료되었습니다.')}catch(e){alert('정기결제 등록 실패: '+e.message)}}if(location.pathname==='/payment/success'||q.has('paymentKey')){try{await req('/api/billing/confirm',{paymentKey:q.get('paymentKey'),orderId:q.get('orderId'),amount:Number(q.get('amount'))});history.replaceState({},'', '/');await loadAccount();alert('PRO 결제가 확인되어 30일 이용이 시작되었습니다.')}catch(e){alert('결제 승인 확인 실패: '+e.message)}}}else if(location.pathname==='/billing/fail'){alert('카드 등록 실패: '+(new URLSearchParams(location.search).get('message')||'등록 실패'))}else if(location.pathname==='/payment/fail'){alert('결제가 완료되지 않았습니다: '+(new URLSearchParams(location.search).get('message')||'결제 실패'))}})();
</script><footer style="max-width:1100px;margin:24px auto;padding:12px 22px;color:#64748b;font-size:13px"><a href="/terms">이용약관</a> · <a href="/privacy">개인정보 처리방침</a></footer></html>'''

class Handler(BaseHTTPRequestHandler):
    server_version='PriceTrackerCloud/1.4'
    def log_message(self,fmt,*args): print('%s - %s'%(self.address_string(),fmt%args),flush=True)
    def send_json(self,obj,status=200,headers=None):
        raw=json.dumps(obj,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','strict-origin-when-cross-origin');self.send_header('Permissions-Policy','camera=(), microphone=(), geolocation=()');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' https://js.tosspayments.com 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' https://api.tosspayments.com")
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.end_headers();self.wfile.write(raw)
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n>20000: raise ValueError('요청 크기가 너무 큽니다.')
        return json.loads(self.rfile.read(n) or b'{}')
    def session(self):
        cookie=self.headers.get('Cookie','')
        m=re.search(r'(?:^|; )pt_session=([a-f0-9]{64})',cookie)
        if not m:return None
        token=m.group(1); th=hashlib.sha256(token.encode()).hexdigest()
        with connect() as c:
            row=c.execute('SELECT s.*,u.email FROM sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=?',(th,)).fetchone()
            if not row:return None
            if row['expires_at'] < now():c.execute('DELETE FROM sessions WHERE token_hash=?',(th,));return None
            return dict(row)
    def require_csrf(self,s):
        if not s or not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),s['csrf']): raise PermissionError('세션이 만료되었거나 보안 토큰이 올바르지 않습니다.')
    def do_GET(self):
        path=urlparse(self.path).path
        if path == '/manifest.webmanifest':
            raw=json.dumps({'id':'/','name':'가격 추적기','short_name':'가격 추적기','description':'국내 쇼핑몰 상품 가격 추적 및 목표가 알림','start_url':'/','scope':'/','display':'standalone','background_color':'#f4f6fa','theme_color':'#2458d3','lang':'ko','icons':[{'src':'/icons/icon-192.png','sizes':'192x192','type':'image/png','purpose':'any'},{'src':'/icons/icon-512.png','sizes':'512x512','type':'image/png','purpose':'any'}]},ensure_ascii=False).encode()
            self.send_response(200);self.send_header('Content-Type','application/manifest+json; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','public, max-age=3600');self.end_headers();self.wfile.write(raw);return
        if path == '/sw.js':
            raw=b"""const CACHE='price-tracker-shell-v14';const SHELL=['/'];self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting())));self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));self.addEventListener('fetch',e=>{const u=new URL(e.request.url);if(e.request.method!=='GET'||u.origin!==location.origin||u.pathname.startsWith('/api/'))return;if(e.request.mode==='navigate'){e.respondWith(fetch(e.request).then(r=>{const copy=r.clone();caches.open(CACHE).then(c=>c.put('/',copy));return r}).catch(()=>caches.match('/')));}});"""
            self.send_response(200);self.send_header('Content-Type','application/javascript; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-cache');self.send_header('Service-Worker-Allowed','/');self.end_headers();self.wfile.write(raw);return
        if path in ('/icons/icon-192.png','/icons/icon-512.png'):
            icon_path=Path(__file__).parent / path.lstrip('/')
            if not icon_path.is_file():return self.send_json({'error':'not found'},404)
            raw=icon_path.read_bytes();self.send_response(200);self.send_header('Content-Type','image/png');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','public, max-age=2592000, immutable');self.end_headers();self.wfile.write(raw);return
        if path in ('/terms','/privacy'):
            title = '서비스 이용약관' if path == '/terms' else '개인정보 처리방침'
            if path == '/terms':
                content = '''<h2>제1조 목적</h2><p>본 약관은 가격 추적 서비스의 이용 조건과 운영자 및 이용자의 권리·의무를 정합니다.</p>
<h2>제2조 서비스</h2><p>서비스는 이용자가 등록한 공개 상품 페이지의 가격 정보를 일정 간격으로 확인하고 가격 기록 및 알림을 제공하는 것을 목표로 합니다. 사이트 구조 변경, 접근 제한, 옵션·배송비 표시 방식 등에 따라 가격 확인이 실패하거나 실제 구매 가격과 다를 수 있습니다.</p>
<h2>제3조 계정과 이용자 책임</h2><p>이용자는 정확한 이메일을 제공하고 계정 보안을 관리해야 합니다. 타인의 권리를 침해하거나 서비스에 과도한 부하를 주는 행위를 해서는 안 됩니다.</p>
<h2>제4조 요금·결제·해지</h2><p>유료 요금, 제공 기능, 결제 주기 및 해지 조건은 결제 화면에 표시된 내용을 따릅니다. 자동결제 해지는 다음 결제부터 적용되며 이미 결제된 이용기간의 처리와 환불은 관계 법령 및 결제 화면의 고지에 따릅니다.</p>
<h2>제5조 서비스 변경·중단</h2><p>점검, 장애, 외부 쇼핑몰 또는 결제·메일 사업자의 정책 변경 등으로 서비스가 변경되거나 일시 중단될 수 있습니다.</p>
<h2>제6조 면책</h2><p>가격 정보는 참고용이며 구매 가능 여부와 최종 결제 금액을 보증하지 않습니다. 이용자는 구매 전 판매처에서 상품·옵션·배송비·재고·가격을 직접 확인해야 합니다.</p>
<h2>제7조 문의</h2><p>운영자 및 문의처: [사업자명 / 이메일을 실제 정보로 교체]</p>'''
            else:
                content = '''<h2>1. 처리하는 개인정보</h2><p>회원 이메일, 비밀번호 해시, 가입·로그인 관련 기록, 등록 상품 URL·상품명·목표가, 가격 확인 이력, 알림 기록, 요금제·결제 식별정보를 서비스 제공에 필요한 범위에서 처리할 수 있습니다.</p>
<h2>2. 처리 목적</h2><p>회원 식별·인증, 상품 가격 확인, 알림 발송, 결제·구독 관리, 부정 이용 방지, 장애 분석 및 고객 문의 대응을 위해 처리합니다.</p>
<h2>3. 보유 및 이용 기간</h2><p>개인정보는 처리 목적 달성 또는 회원 탈퇴 시 지체 없이 파기하는 것을 원칙으로 합니다. 다만 결제·계약·분쟁 관련 정보는 관련 법령이 정한 기간 동안 별도 보관할 수 있습니다. 실제 보유기간과 파기 절차를 운영 정책에 맞게 확정해 기재해야 합니다.</p>
<h2>4. 처리 위탁 및 외부 서비스</h2><p>서비스 운영 과정에서 이메일 발송(SMTP 사업자), 결제 처리(Toss Payments), 서버·호스팅 사업자를 이용할 수 있습니다. 실제 이용 사업자명, 위탁 업무, 국외 이전 여부 및 관련 사항을 운영 환경에 맞게 확정해 기재해야 합니다.</p>
<h2>5. 이용자의 권리</h2><p>이용자는 개인정보 열람·정정·삭제·처리정지를 요청할 수 있습니다. 계정 데이터 내보내기는 로그인 후 <code>/api/account/export</code>에서 JSON 형식으로 요청할 수 있습니다.</p>
<h2>6. 안전성 확보</h2><p>비밀번호 해시, 세션 보호, 접근통제 및 백업 등 기술적·관리적 조치를 적용합니다. 구체적인 조치는 실제 운영 환경에 맞춰 관리합니다.</p>
<h2>7. 개인정보 보호 문의</h2><p>개인정보 보호책임자 및 문의처: [담당자 / 이메일을 실제 정보로 교체]</p>'''
            raw = ('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>'+title+'</title><style>body{font:16px/1.7 system-ui;max-width:850px;margin:36px auto;padding:0 18px;color:#222}h1{font-size:28px}h2{font-size:19px;margin-top:28px}a{color:#1358a5}.notice{background:#fff4d6;padding:12px;border-radius:8px}</style><h1>'+title+'</h1><p class="notice">출시 전 안내: 이 문서는 운영 초안입니다. 대괄호 항목을 실제 사업자 정보로 교체하고, 실제 데이터 처리·결제·위탁 현황과 일치하도록 법률 검토 후 게시해야 합니다.</p>'+content+'<hr><p><a href="/">가격 추적기로 돌아가기</a></p></html>').encode()
            self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.send_header('X-Frame-Options','DENY'); self.send_header('Referrer-Policy','strict-origin-when-cross-origin'); self.end_headers(); self.wfile.write(raw); return
        if path == '/api/account/export':
            s = self.session()
            if not s: return self.send_json({'error':'로그인이 필요합니다.'},401)
            with connect() as c:
                user = dict(c.execute('SELECT id,email,created_at,plan,plan_expires,role,email_verified FROM users WHERE id=?',(s['user_id'],)).fetchone())
                products = [dict(r) for r in c.execute('SELECT id,name,url,target_price,shipping,current_price,last_checked,last_error,created_at FROM products WHERE user_id=? ORDER BY id',(s['user_id'],))]
                product_ids = [x['id'] for x in products]
                history = []
                if product_ids:
                    marks=','.join('?' for _ in product_ids)
                    history=[dict(r) for r in c.execute(f'SELECT product_id,price,checked_at FROM price_history WHERE product_id IN ({marks}) ORDER BY id',(product_ids,))]
                alerts=[dict(r) for r in c.execute('SELECT product_id,message,created_at,emailed FROM alerts WHERE user_id=? ORDER BY id',(s['user_id'],))]
                sub_events=[dict(r) for r in c.execute('SELECT event,plan,note,created_at FROM subscription_events WHERE user_id=? ORDER BY id',(s['user_id'],))]
                payments=[dict(r) for r in c.execute('SELECT order_id,plan,amount,status,created_at,paid_at FROM payment_orders WHERE user_id=? ORDER BY id',(s['user_id'],))]
            return self.send_json({'exported_at':now(),'user':user,'products':products,'price_history':history,'alerts':alerts,'subscription_events':sub_events,'payments':payments})
        if path=='/verify':
            token=urllib.parse.parse_qs(urlparse(self.path).query).get('token',[''])[0]
            thtok=hashlib.sha256(token.encode()).hexdigest()
            with connect() as c:
                t=c.execute("SELECT * FROM account_tokens WHERE token_hash=? AND purpose='verify' AND used_at='' AND expires_at>=?",(thtok,now())).fetchone()
                if t:
                    c.execute('UPDATE users SET email_verified=1 WHERE id=?',(t['user_id'],));c.execute('UPDATE account_tokens SET used_at=? WHERE id=?',(now(),t['id']))
                    message='이메일 인증이 완료되었습니다. 이제 로그인할 수 있습니다.'
                else: message='인증 링크가 만료되었거나 이미 사용되었습니다.'
            raw=('<!doctype html><meta charset=utf-8><title>이메일 인증</title><h2>'+html.escape(message)+'</h2><a href="/">가격 추적기로 돌아가기</a>').encode()
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.end_headers();self.wfile.write(raw);return
        if path=='/reset':
            token=urllib.parse.parse_qs(urlparse(self.path).query).get('token',[''])[0]
            raw=('<!doctype html><meta charset=utf-8><meta name=viewport content=width=device-width><title>비밀번호 재설정</title><h2>새 비밀번호 설정</h2><input id=p type=password minlength=12 placeholder="새 비밀번호 12자 이상"><button onclick="fetch(\'/api/password/reset\',{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify({token:'+json.dumps(token)+',password:document.getElementById(\'p\').value})}).then(r=>r.json()).then(j=>alert(j.message||j.error))">변경</button>').encode()
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.end_headers();self.wfile.write(raw);return
        if path in ('/','/payment/success','/payment/fail','/billing/issue','/billing/fail'):
            raw=PAGE.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','strict-origin-when-cross-origin');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' https://js.tosspayments.com 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' https://api.tosspayments.com");self.end_headers();self.wfile.write(raw);return
        if path=='/health':
            try:
                with connect() as c: c.execute('SELECT 1')
                with connect() as c:
                    integrity=c.execute('PRAGMA quick_check').fetchone()[0]
                    counts={'users':c.execute('SELECT COUNT(*) n FROM users').fetchone()['n'],'products':c.execute('SELECT COUNT(*) n FROM products').fetchone()['n'],'failed_subscriptions':c.execute("SELECT COUNT(*) n FROM subscriptions WHERE status IN ('past_due','payment_failed')").fetchone()['n']}
                return self.send_json({'status':'ok' if integrity=='ok' else 'degraded','database':'ok' if integrity=='ok' else integrity,'billing_enabled':BILLING_ENABLED,'smtp_enabled':bool(os.environ.get('SMTP_HOST')),'counts':counts,'time':now()})
            except Exception as e:
                record_system_event('ERROR','health_check_failed',str(e)); return self.send_json({'status':'error'},503)
        s=self.session()
        if path=='/api/me':return self.send_json({'email':s['email'],'csrf':s['csrf']} if s else {'error':'로그인이 필요합니다.'},200 if s else 401)
        if path=='/api/data':
            if not s:return self.send_json({'error':'로그인이 필요합니다.'},401)
            with connect() as c:
                ps=[dict(x) for x in c.execute('SELECT * FROM products WHERE user_id=? ORDER BY id DESC',(s['user_id'],))]
                als=[dict(x) for x in c.execute('SELECT * FROM alerts WHERE user_id=? ORDER BY id DESC LIMIT 30',(s['user_id'],))]
            return self.send_json({'products':ps,'alerts':als})
        if path=='/api/account':
            if not s:return self.send_json({'error':'로그인이 필요합니다.'},401)
            with connect() as c:
                u=c.execute('SELECT email,plan,plan_expires,role FROM users WHERE id=?',(s['user_id'],)).fetchone()
                count=c.execute('SELECT COUNT(*) n FROM products WHERE user_id=?',(s['user_id'],)).fetchone()['n']
            plan=u['plan'] if u['plan'] in PLAN_LIMITS else 'free'
            if u['plan_expires'] and u['plan_expires'] < now(): plan='free'
            return self.send_json({'email':u['email'],'plan':plan,'role':u['role'],'used':count,'limit':PLAN_LIMITS[plan],'expires':u['plan_expires'],'pro_price':PRO_PRICE_KRW,'payments_enabled':PAYMENTS_ENABLED})
        if path=='/api/admin/overview':
            if not s:return self.send_json({'error':'로그인이 필요합니다.'},401)
            with connect() as c:
                me=c.execute('SELECT role FROM users WHERE id=?',(s['user_id'],)).fetchone()
                if not me or me['role']!='admin':return self.send_json({'error':'관리자 권한이 필요합니다.'},403)
                users=c.execute('SELECT COUNT(*) n FROM users').fetchone()['n']
                pro=c.execute("SELECT COUNT(*) n FROM users WHERE plan='pro' AND (plan_expires='' OR plan_expires>?)",(now(),)).fetchone()['n']
                paid_gross=c.execute("SELECT COALESCE(SUM(amount),0) n FROM payment_orders WHERE status IN ('paid','partially_refunded')").fetchone()['n']
                paid_refunds=c.execute('SELECT COALESCE(SUM(amount),0) n FROM refund_records').fetchone()['n']
                paid=max(0,paid_gross-paid_refunds)
                month=datetime.now(timezone.utc).strftime('%Y-%m')
                monthly_gross=c.execute("SELECT COALESCE(SUM(amount),0) n FROM payment_orders WHERE status IN ('paid','partially_refunded') AND substr(paid_at,1,7)=?",(month,)).fetchone()['n']
                monthly_refunds=c.execute("SELECT COALESCE(SUM(amount),0) n FROM refund_records WHERE substr(created_at,1,7)=?",(month,)).fetchone()['n']
                monthly=max(0,monthly_gross-monthly_refunds)
                active=c.execute("SELECT COUNT(*) n FROM subscriptions WHERE status='active'").fetchone()['n']
                past_due=c.execute("SELECT COUNT(*) n FROM subscriptions WHERE status='past_due'").fetchone()['n']
                terminal_subs=c.execute("SELECT COUNT(*) n FROM subscriptions WHERE status='payment_failed'").fetchone()['n']
                failed=c.execute("SELECT COUNT(*) n FROM payment_orders WHERE status='failed'").fetchone()['n']
                rows=[dict(x) for x in c.execute("SELECT p.order_id,p.amount,p.status,p.payment_key,p.created_at,p.paid_at,u.email FROM payment_orders p JOIN users u ON u.id=p.user_id ORDER BY p.id DESC LIMIT 30")]
                events=[dict(x) for x in c.execute("SELECT level,event,detail,created_at FROM system_events ORDER BY id DESC LIMIT 12")]
                errors=c.execute("SELECT COUNT(*) n FROM system_events WHERE level='ERROR' AND created_at>=?",((datetime.now(timezone.utc)-timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S UTC'),)).fetchone()['n']
            return self.send_json({'users':users,'pro_users':pro,'paid_total':paid,'month_total':monthly,'active_subscriptions':active,'past_due_subscriptions':past_due,'failed_subscriptions':terminal_subs,'failed_payments':failed,'errors_24h':errors,'payments':rows,'events':events})
        if path=='/api/admin/users':
            if not s:return self.send_json({'error':'로그인이 필요합니다.'},401)
            with connect() as c:
                me=c.execute('SELECT role FROM users WHERE id=?',(s['user_id'],)).fetchone()
                if not me or me['role']!='admin':return self.send_json({'error':'관리자 권한이 필요합니다.'},403)
                rows=[dict(r) for r in c.execute('''SELECT u.id,u.email,u.email_verified,u.plan,u.plan_expires,u.role,u.created_at,COUNT(p.id) product_count FROM users u LEFT JOIN products p ON p.user_id=u.id GROUP BY u.id ORDER BY u.id DESC LIMIT 200''')]
            return self.send_json({'users':rows})
        return self.send_json({'error':'Not found'},404)
    def do_POST(self):
        path=urlparse(self.path).path
        try:
            if path=='/webhooks/toss':
                d=self.body(); event=str(d.get('eventType','')); data=d.get('data') or {}; key=str(data.get('paymentKey',''))
                event_key=hashlib.sha256((event+'|'+str(d.get('createdAt',''))+'|'+key+'|'+json.dumps(data,sort_keys=True)).encode()).hexdigest()
                with connect() as c:c.execute('INSERT OR IGNORE INTO billing_events(event_key,event_type,payload,created_at) VALUES(?,?,?,?)',(event_key,event,json.dumps(d,ensure_ascii=False)[:10000],now()))
                if event=='PAYMENT_STATUS_CHANGED' and key and TOSS_SECRET_KEY:
                    try:
                        live=toss_api('GET','/v1/payments/'+urllib.parse.quote(key,safe=''))
                        if live.get('paymentKey')==key and live.get('status') in ('CANCELED','PARTIAL_CANCELED'):
                            with connect() as c:c.execute('UPDATE payment_orders SET status=? WHERE payment_key=?',('refunded' if live.get('status')=='CANCELED' else 'partially_refunded',key))
                    except Exception as e:record_system_event('WARN','webhook_reconcile_failed',str(e))
                return self.send_json({'ok':True})
            d=self.body()
            if path=='/api/password/forgot':
                email=str(d.get('email','')).strip().lower()
                if re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email):
                    with connect() as c: u=c.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()
                    if u and os.environ.get('SMTP_HOST','').strip():
                        rawtok=secrets.token_urlsafe(32); thtok=hashlib.sha256(rawtok.encode()).hexdigest(); exp=(datetime.now(timezone.utc)+timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S UTC')
                        with connect() as c:
                            c.execute("UPDATE account_tokens SET used_at=? WHERE user_id=? AND purpose='reset' AND used_at=''",(now(),u['id']))
                            c.execute("INSERT INTO account_tokens(user_id,token_hash,purpose,expires_at,created_at) VALUES(?,?,'reset',?,?)",(u['id'],thtok,exp,now()))
                        link=(PUBLIC_BASE_URL or 'http://localhost:8080')+'/reset?token='+urllib.parse.quote(rawtok)
                        try: send_email(email,'가격 추적기 비밀번호 재설정','30분 안에 아래 링크를 열어 새 비밀번호를 설정해 주세요.\n'+link)
                        except Exception as mail_err: record_system_event('ERROR','password_reset_email_failed',str(mail_err))
                return self.send_json({'ok':True,'message':'해당 계정이 존재하면 비밀번호 재설정 안내를 이메일로 보냈습니다.'})
            if path=='/api/password/reset':
                token=str(d.get('token','')).strip(); password=str(d.get('password',''))
                if len(password)<12: raise ValueError('새 비밀번호는 12자 이상이어야 합니다.')
                thtok=hashlib.sha256(token.encode()).hexdigest()
                with connect() as c:
                    t=c.execute("SELECT * FROM account_tokens WHERE token_hash=? AND purpose='reset' AND used_at='' AND expires_at>=?",(thtok,now())).fetchone()
                    if not t:return self.send_json({'error':'재설정 링크가 만료되었거나 올바르지 않습니다.'},400)
                    c.execute('UPDATE users SET password_hash=? WHERE id=?',(pw_hash(password),t['user_id']))
                    c.execute('UPDATE account_tokens SET used_at=? WHERE id=?',(now(),t['id']))
                    c.execute('DELETE FROM sessions WHERE user_id=?',(t['user_id'],))
                return self.send_json({'ok':True,'message':'비밀번호가 변경되었습니다. 새 비밀번호로 로그인해 주세요.'})
            if path in ('/api/register','/api/login'):
                email=str(d.get('email','')).strip().lower(); password=str(d.get('password',''))
                if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email):return self.send_json({'error':'올바른 이메일 주소를 입력해 주세요.'},400)
                if len(password)<8:return self.send_json({'error':'비밀번호는 8자 이상이어야 합니다.'},400)
                ip=(self.client_address[0] if self.client_address else 'unknown')[:64]
                cutoff=(datetime.now(timezone.utc)-timedelta(minutes=15)).strftime('%Y-%m-%d %H:%M:%S UTC')
                with connect() as c:
                    recent=c.execute('SELECT COUNT(*) n FROM auth_attempts WHERE ip=? AND action=? AND succeeded=0 AND created_at>=?',(ip,path,cutoff)).fetchone()['n']
                    if recent>=10:return self.send_json({'error':'로그인 시도가 너무 많습니다. 15분 후 다시 시도해 주세요.'},429)
                    user=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
                    if path.endswith('register'):
                        if user:
                            c.execute('INSERT INTO auth_attempts(ip,action,succeeded,created_at) VALUES(?,?,0,?)',(ip,path,now()))
                            return self.send_json({'error':'이미 가입된 이메일입니다.'},409)
                        role='admin' if ADMIN_EMAIL and email==ADMIN_EMAIL else 'user'; plan='admin' if role=='admin' else 'free'
                        verified=0 if os.environ.get('REQUIRE_EMAIL_VERIFICATION','true').lower()=='true' and os.environ.get('SMTP_HOST','').strip() else 1
                        cur=c.execute('INSERT INTO users(email,password_hash,created_at,role,plan,email_verified) VALUES(?,?,?,?,?,?)',(email,pw_hash(password),now(),role,plan,verified));uid=cur.lastrowid
                        if not verified:
                            rawtok=secrets.token_urlsafe(32); thtok=hashlib.sha256(rawtok.encode()).hexdigest(); exp=(datetime.now(timezone.utc)+timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S UTC')
                            c.execute("INSERT INTO account_tokens(user_id,token_hash,purpose,expires_at,created_at) VALUES(?,?,'verify',?,?)",(uid,thtok,exp,now()))
                            verify_link=(PUBLIC_BASE_URL or 'http://localhost:8080')+'/verify?token='+urllib.parse.quote(rawtok)
                            try: send_email(email,'가격 추적기 이메일 인증', '아래 링크를 열어 이메일 인증을 완료해 주세요. (24시간 유효)\n'+verify_link)
                            except Exception as mail_err: record_system_event('ERROR','verification_email_failed',str(mail_err))
                            return self.send_json({'error':'인증 링크를 이메일로 보냈습니다. 이메일 인증 후 로그인해 주세요.'},202)
                    else:
                        if not user or not pw_verify(password,user['password_hash']):
                            c.execute('INSERT INTO auth_attempts(ip,action,succeeded,created_at) VALUES(?,?,0,?)',(ip,path,now()))
                            return self.send_json({'error':'이메일 또는 비밀번호가 올바르지 않습니다.'},401)
                        if not user['email_verified']:
                            c.execute('INSERT INTO auth_attempts(ip,action,succeeded,created_at) VALUES(?,?,0,?)',(ip,path,now()))
                            return self.send_json({'error':'이메일 인증이 필요합니다. 가입 시 받은 인증 링크를 확인해 주세요.'},403)
                        uid=user['id']
                    c.execute('INSERT INTO auth_attempts(ip,action,succeeded,created_at) VALUES(?,?,1,?)',(ip,path,now()))
                    c.execute('DELETE FROM auth_attempts WHERE created_at<?',( (datetime.now(timezone.utc)-timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S UTC'),))
                    token=secrets.token_hex(32);csrf=secrets.token_urlsafe(24);th=hashlib.sha256(token.encode()).hexdigest();expires=(datetime.now(timezone.utc)+timedelta(days=14)).strftime('%Y-%m-%d %H:%M:%S UTC')
                    c.execute('INSERT INTO sessions(token_hash,user_id,csrf,expires_at) VALUES(?,?,?,?)',(th,uid,csrf,expires))
                secure='; Secure' if os.environ.get('COOKIE_SECURE','true').lower()=='true' else ''
                return self.send_json({'csrf':csrf},200,{'Set-Cookie':f'pt_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=1209600{secure}'})
            s=self.session();self.require_csrf(s)
            if path=='/api/logout':
                token=re.search(r'(?:^|; )pt_session=([a-f0-9]{64})',self.headers.get('Cookie','')).group(1)
                with connect() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
                secure='; Secure' if os.environ.get('COOKIE_SECURE','true').lower()=='true' else ''
                return self.send_json({'ok':True},200,{'Set-Cookie':f'pt_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0{secure}'})
            if path=='/api/billing/setup':
                if not BILLING_ENABLED:return self.send_json({'error':'자동결제 키 또는 HTTPS 주소가 설정되지 않았습니다.'},503)
                with connect() as c:
                    existing=c.execute("SELECT id FROM subscriptions WHERE user_id=? AND status IN ('active','past_due')",(s['user_id'],)).fetchone()
                    if existing:return self.send_json({'error':'이미 자동구독이 등록되어 있습니다.'},409)
                    row=c.execute('SELECT customer_key FROM billing_customers WHERE user_id=?',(s['user_id'],)).fetchone()
                    customer_key=row['customer_key'] if row else secrets.token_urlsafe(24)
                    c.execute('INSERT OR IGNORE INTO billing_customers(user_id,customer_key,created_at) VALUES(?,?,?)',(s['user_id'],customer_key,now()))
                return self.send_json({'clientKey':TOSS_CLIENT_KEY,'customerKey':customer_key,'successUrl':PUBLIC_BASE_URL+'/billing/issue','failUrl':PUBLIC_BASE_URL+'/billing/fail'})
            if path=='/api/billing/issue':
                if not BILLING_ENABLED:return self.send_json({'error':'자동결제가 설정되지 않았습니다.'},503)
                auth_key=str(d.get('authKey','')).strip();customer_key=str(d.get('customerKey','')).strip()
                if not auth_key or len(auth_key)>300:raise ValueError('인증 키가 올바르지 않습니다.')
                with connect() as c:
                    row=c.execute('SELECT customer_key FROM billing_customers WHERE user_id=?',(s['user_id'],)).fetchone()
                    user=c.execute('SELECT email FROM users WHERE id=?',(s['user_id'],)).fetchone()
                if not row or not hmac.compare_digest(row['customer_key'],customer_key):return self.send_json({'error':'고객 인증 정보가 일치하지 않습니다.'},403)
                issued=toss_api('POST','/v1/billing/authorizations/issue',{'authKey':auth_key,'customerKey':customer_key},timeout=30)
                billing_key=issued.get('billingKey','')
                if not billing_key:raise ValueError('빌링키 발급에 실패했습니다.')
                enc=encrypt_billing_key(billing_key);nxt=(datetime.now(timezone.utc)+timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S UTC')
                with connect() as c:
                    c.execute("INSERT INTO subscriptions(user_id,customer_key,billing_key_enc,status,amount,next_billing_at,created_at,updated_at) VALUES(?,?,?,'pending',?,?,?,?)",(s['user_id'],customer_key,enc,PRO_PRICE_KRW,nxt,now(),now()))
                    sid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                sub={'id':sid,'user_id':s['user_id'],'customer_key':customer_key,'billing_key_enc':enc,'amount':PRO_PRICE_KRW,'email':user['email']}
                try:
                    charge_subscription(sub)
                    return self.send_json({'ok':True,'plan':'pro'})
                except Exception as e:
                    with connect() as c:c.execute("UPDATE subscriptions SET status='past_due',updated_at=? WHERE id=?",(now(),sid))
                    record_system_event('ERROR','initial_billing_failed',str(e))
                    return self.send_json({'error':'카드는 등록됐지만 첫 결제에 실패했습니다. 지원팀에 문의해 주세요.'},402)
            if path=='/api/billing/cancel':
                with connect() as c:
                    sub=c.execute("SELECT * FROM subscriptions WHERE user_id=? AND status IN ('active','past_due') ORDER BY id DESC LIMIT 1",(s['user_id'],)).fetchone()
                    if not sub:return self.send_json({'error':'활성 자동구독이 없습니다.'},404)
                    c.execute("UPDATE subscriptions SET status='canceled',updated_at=? WHERE id=?",(now(),sub['id']))
                    c.execute("INSERT INTO subscription_events(user_id,event,plan,note,created_at) VALUES(?,?,?,?,?)",(s['user_id'],'subscription_canceled','pro','자동갱신 해지; 현재 이용기간 유지',now()))
                return self.send_json({'ok':True})
            if path=='/api/admin/refund':
                with connect() as c:
                    me=c.execute('SELECT role FROM users WHERE id=?',(s['user_id'],)).fetchone()
                    if not me or me['role']!='admin':return self.send_json({'error':'관리자 권한이 필요합니다.'},403)
                    payment_key=str(d.get('paymentKey','')).strip();reason=str(d.get('reason','')).strip()[:200]
                    order=c.execute("SELECT * FROM payment_orders WHERE payment_key=? AND status='paid'",(payment_key,)).fetchone()
                    if not order:return self.send_json({'error':'환불 가능한 결제가 없습니다.'},404)
                    if not reason:raise ValueError('환불 사유를 입력해 주세요.')
                result=toss_api('POST','/v1/payments/'+urllib.parse.quote(payment_key,safe='')+'/cancel',{'cancelReason':reason},timeout=30)
                status='refunded' if result.get('status')=='CANCELED' else 'partially_refunded'
                refunded=sum(int(x.get('cancelAmount',0)) for x in result.get('cancels',[]) if x.get('cancelStatus')=='DONE')
                with connect() as c:
                    c.execute('UPDATE payment_orders SET status=?,provider_payload=? WHERE payment_key=?',(status,json.dumps(result,ensure_ascii=False)[:20000],payment_key))
                    c.execute('INSERT INTO refund_records(payment_key,user_id,amount,reason,status,created_at,provider_payload) VALUES(?,?,?,?,?,?,?)',(payment_key,order['user_id'],refunded,reason,status,now(),json.dumps(result,ensure_ascii=False)[:20000]))
                    if status=='refunded':c.execute("UPDATE subscriptions SET status='canceled',updated_at=? WHERE user_id=? AND last_payment_key=?",(now(),order['user_id'],payment_key))
                return self.send_json({'ok':True,'status':status,'refunded_amount':refunded})
            if path=='/api/billing/order':
                if not PAYMENTS_ENABLED: return self.send_json({'error':'결제 기능이 아직 설정되지 않았습니다. 운영자가 PG 키와 HTTPS 주소를 설정해야 합니다.'},503)
                with connect() as c:
                    u=c.execute('SELECT email,role FROM users WHERE id=?',(s['user_id'],)).fetchone()
                    if not u: return self.send_json({'error':'계정을 찾을 수 없습니다.'},404)
                    order_id='pt_'+secrets.token_urlsafe(18).replace('-','').replace('_','')[:28]
                    c.execute('INSERT INTO payment_orders(user_id,order_id,plan,amount,status,created_at) VALUES(?,?,?,?,?,?)',(s['user_id'],order_id,'pro',PRO_PRICE_KRW,'pending',now()))
                return self.send_json({'clientKey':TOSS_CLIENT_KEY,'orderId':order_id,'amount':PRO_PRICE_KRW,'orderName':'가격 추적기 PRO 30일 이용권','customerEmail':u['email'],'successUrl':PUBLIC_BASE_URL+'/payment/success','failUrl':PUBLIC_BASE_URL+'/payment/fail'})
            if path=='/api/billing/confirm':
                if not PAYMENTS_ENABLED: return self.send_json({'error':'결제 기능이 설정되지 않았습니다.'},503)
                payment_key=str(d.get('paymentKey','')).strip();order_id=str(d.get('orderId','')).strip()
                try: amount=int(d.get('amount'))
                except Exception: raise ValueError('결제 금액이 올바르지 않습니다.')
                if not payment_key or len(payment_key)>200 or not re.fullmatch(r'pt_[A-Za-z0-9]{12,40}',order_id): raise ValueError('결제 승인 정보가 올바르지 않습니다.')
                with connect() as c:
                    order=c.execute('SELECT * FROM payment_orders WHERE order_id=? AND user_id=?',(order_id,s['user_id'])).fetchone()
                    if not order: return self.send_json({'error':'주문을 찾을 수 없습니다.'},404)
                    if order['status']=='paid': return self.send_json({'ok':True,'alreadyPaid':True})
                    if order['status']!='pending' or amount!=int(order['amount']): return self.send_json({'error':'주문 상태 또는 결제 금액이 일치하지 않습니다.'},409)
                payload=json.dumps({'paymentKey':payment_key,'orderId':order_id,'amount':amount}).encode()
                auth=base64.b64encode((TOSS_SECRET_KEY+':').encode()).decode()
                request=urllib.request.Request('https://api.tosspayments.com/v1/payments/confirm',data=payload,headers={'Authorization':'Basic '+auth,'Content-Type':'application/json'},method='POST')
                try:
                    with urllib.request.urlopen(request,timeout=20) as response: result=json.loads(response.read(100000).decode('utf-8'))
                except urllib.error.HTTPError as e:
                    detail=e.read(4000).decode('utf-8','replace')
                    with connect() as c:c.execute("UPDATE payment_orders SET status='failed',provider_payload=? WHERE order_id=? AND status='pending'",(detail[:4000],order_id))
                    return self.send_json({'error':'결제사 승인에 실패했습니다. 결제 내역을 확인한 뒤 다시 시도해 주세요.'},402)
                if result.get('status')!='DONE' or result.get('orderId')!=order_id or int(result.get('totalAmount',-1))!=amount :
                    return self.send_json({'error':'결제사 승인 응답의 상태·주문번호·금액 검증에 실패했습니다.'},409)
                paid_at=now()
                with connect() as c:
                    order=c.execute('SELECT * FROM payment_orders WHERE order_id=? AND user_id=?',(order_id,s['user_id'])).fetchone()
                    if order['status']=='paid': return self.send_json({'ok':True,'alreadyPaid':True})
                    if order['status']!='pending': return self.send_json({'error':'이미 처리된 주문입니다.'},409)
                    u=c.execute('SELECT plan,plan_expires FROM users WHERE id=?',(s['user_id'],)).fetchone()
                    base=datetime.now(timezone.utc)
                    if u['plan']=='pro' and u['plan_expires']:
                        try: base=max(base,datetime.strptime(u['plan_expires'],'%Y-%m-%d %H:%M:%S UTC').replace(tzinfo=timezone.utc))
                        except ValueError: pass
                    expires=(base+timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S UTC')
                    c.execute("UPDATE payment_orders SET status='paid',payment_key=?,paid_at=?,provider_payload=? WHERE order_id=? AND status='pending'",(payment_key,paid_at,json.dumps({'status':result.get('status'),'orderId':result.get('orderId'),'totalAmount':result.get('totalAmount'),'paymentKey':result.get('paymentKey'),'method':result.get('method'),'approvedAt':result.get('approvedAt')},ensure_ascii=False),order_id))
                    if c.total_changes < 1: return self.send_json({'error':'주문 처리 상태를 갱신하지 못했습니다.'},409)
                    c.execute("UPDATE users SET plan='pro',plan_expires=? WHERE id=?",(expires,s['user_id']))
                    c.execute('INSERT INTO subscription_events(user_id,event,plan,note,created_at) VALUES(?,?,?,?,?)',(s['user_id'],'payment_confirmed','pro',f'Toss Payments 주문 {order_id}, {amount}원, 30일',paid_at))
                return self.send_json({'ok':True,'plan':'pro','expires':expires})
            if path=='/api/admin/plan':
                with connect() as c:
                    me=c.execute('SELECT role FROM users WHERE id=?',(s['user_id'],)).fetchone()
                    if not me or me['role']!='admin':return self.send_json({'error':'관리자 권한이 필요합니다.'},403)
                    uid=int(d.get('user_id',0));plan=str(d.get('plan','free'))
                    if plan not in ('free','pro'):raise ValueError('플랜은 free 또는 pro만 설정할 수 있습니다.')
                    days=int(d.get('days',30))
                    if days<1 or days>366:raise ValueError('기간은 1~366일이어야 합니다.')
                    user=c.execute('SELECT id FROM users WHERE id=?',(uid,)).fetchone()
                    if not user:raise ValueError('사용자를 찾을 수 없습니다.')
                    expires='' if plan=='free' else (datetime.now(timezone.utc)+timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S UTC')
                    c.execute('UPDATE users SET plan=?,plan_expires=? WHERE id=?',(plan,expires,uid))
                    c.execute('INSERT INTO subscription_events(user_id,event,plan,note,created_at) VALUES(?,?,?,?,?)',(uid,'admin_manual_change',plan,f'관리자 수동 변경, {days}일',now()))
                return self.send_json({'ok':True})
            if path=='/api/products/add':
                name=str(d.get('name','')).strip();url=validate_url(str(d.get('url','')).strip());target=int(d['target_price']) if str(d.get('target_price','')).strip() else None;shipping=int(d.get('shipping') or 0)
                if not name or len(name)>160:raise ValueError('상품명은 1~160자로 입력해 주세요.')
                if target is not None and target<1:raise ValueError('목표가는 1원 이상 입력해 주세요.')
                if shipping<0 or shipping>10000000:raise ValueError('배송비를 확인해 주세요.')
                with connect() as c:
                    u=c.execute('SELECT plan,plan_expires,role FROM users WHERE id=?',(s['user_id'],)).fetchone()
                    plan=u['plan'] if u['plan'] in PLAN_LIMITS else 'free'
                    if u['plan_expires'] and u['plan_expires'] < now():plan='free'
                    limit=PLAN_LIMITS['admin'] if u['role']=='admin' else PLAN_LIMITS[plan]
                    used=c.execute('SELECT COUNT(*) n FROM products WHERE user_id=?',(s['user_id'],)).fetchone()['n']
                    if used>=limit:raise ValueError(f'{plan.upper()} 요금제 상품 한도({limit}개)에 도달했습니다. 요금제를 업그레이드해 주세요.')
                    c.execute('INSERT INTO products(user_id,name,url,target_price,shipping,created_at) VALUES(?,?,?,?,?,?)',(s['user_id'],name,url,target,shipping,now()))
                return self.send_json({'ok':True})
            if path=='/api/products/check':
                pid=int(d.get('id',0))
                with connect() as c:
                    product=c.execute('SELECT * FROM products WHERE id=? AND user_id=?',(pid,s['user_id'])).fetchone()
                if not product:return self.send_json({'error':'상품을 찾을 수 없습니다.'},404)
                price=fetch_price(product['url'])
                with connect() as c:
                    c.execute('INSERT INTO price_history(product_id,price,checked_at) VALUES(?,?,?)',(pid,price,now()))
                    c.execute('UPDATE products SET current_price=?,last_checked=?,last_error=? WHERE id=? AND user_id=?',(price,now(),'',pid,s['user_id']))
                    target=product['target_price']; total=price+int(product['shipping'] or 0)
                    if target and total<=int(target):
                        prior=product['current_price']
                        if prior is None or int(prior)+int(product['shipping'] or 0)>int(target):
                            c.execute('INSERT INTO alerts(user_id,product_id,message,created_at) VALUES(?,?,?,?)',(s['user_id'],pid,f\"{product['name']} 목표가 도달: 배송 포함 {total:,}원 (목표 {int(target):,}원)\",now()))
                return self.send_json({'ok':True,'price':price})
            if path=='/api/products/delete':
                pid=int(d.get('id',0))
                with connect() as c:c.execute('DELETE FROM products WHERE id=? AND user_id=?',(pid,s['user_id']))
                return self.send_json({'ok':True})
            return self.send_json({'error':'Not found'},404)
        except PermissionError as e:return self.send_json({'error':str(e)},403)
        except (ValueError,TypeError,KeyError) as e:return self.send_json({'error':str(e)},400)
        except Exception as e:print('Request error:',e,flush=True);return self.send_json({'error':'서버 내부 오류가 발생했습니다.'},500)

if __name__=='__main__':
    if len(APP_SECRET)<32: raise SystemExit('APP_SECRET 환경변수에 32자 이상의 임의 비밀값을 설정해 주세요. .env.example을 참고하세요.')
    init_db()
    threading.Thread(target=price_worker,daemon=True).start()
    if BILLING_ENABLED: threading.Thread(target=billing_worker,daemon=True).start()
    print(f'Price Tracker Cloud listening on {HOST}:{PORT}',flush=True)
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()

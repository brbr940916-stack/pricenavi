#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local Korean shopping price tracker MVP. Python standard library only."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import sqlite3, json, csv, io, webbrowser, threading, time, urllib.request, urllib.parse, urllib.error, re, ipaddress, socket
from datetime import datetime, timedelta

BASE = Path(__file__).resolve().parent
DB = BASE / 'price_tracker.db'
SETTINGS = BASE / 'settings.json'
HOST, PORT = '127.0.0.1', 8765


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute('''CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        shop TEXT NOT NULL DEFAULT '직접 입력',
        url TEXT NOT NULL DEFAULT '',
        target_price INTEGER,
        created_at TEXT NOT NULL
    )''')
    cols = {r['name'] for r in con.execute('PRAGMA table_info(products)').fetchall()}
    for col, decl in [('provider', "TEXT NOT NULL DEFAULT ''"), ('external_id', "TEXT NOT NULL DEFAULT ''"), ('last_checked', "TEXT NOT NULL DEFAULT ''"), ('last_error', "TEXT NOT NULL DEFAULT ''"), ('shipping_price', "INTEGER NOT NULL DEFAULT 0"), ('model_no', "TEXT NOT NULL DEFAULT ''"), ('variant', "TEXT NOT NULL DEFAULT ''"), ('compare_key', "TEXT NOT NULL DEFAULT ''")]:
        if col not in cols: con.execute(f'ALTER TABLE products ADD COLUMN {col} {decl}')
    con.execute('''CREATE TABLE IF NOT EXISTS prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        price INTEGER NOT NULL,
        checked_at TEXT NOT NULL,
        FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
    )''')
    con.execute('''CREATE TABLE IF NOT EXISTS alert_events (id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0, FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE)''')
    con.commit()
    return con


def add_price(con, product_id, price):
    product = con.execute('SELECT name, shop, target_price, shipping_price FROM products WHERE id=?', (product_id,)).fetchone()
    if not product:
        raise ValueError('상품을 찾을 수 없습니다.')
    previous = con.execute('SELECT price FROM prices WHERE product_id=? ORDER BY id DESC LIMIT 1', (product_id,)).fetchone()
    total = int(price) + int(product['shipping_price'] or 0)
    target = product['target_price']
    previous_total = (int(previous['price']) + int(product['shipping_price'] or 0)) if previous else None
    con.execute('INSERT INTO prices(product_id,price,checked_at) VALUES(?,?,?)', (product_id, int(price), now()))
    if target and total <= int(target) and (previous_total is None or previous_total > int(target)):
        msg = f"{product['name']} ({product['shop']}) 배송 포함 {total:,}원 · 목표가 {int(target):,}원"
        con.execute('INSERT INTO alert_events(product_id,message,created_at) VALUES(?,?,?)', (product_id, msg, now()))
    return bool(target and total <= int(target))

def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def rows():
    con = db()
    products = con.execute('''SELECT p.*, (SELECT price FROM prices WHERE product_id=p.id ORDER BY id DESC LIMIT 1) current_price,
      (SELECT MIN(price) FROM prices WHERE product_id=p.id) lowest_price,
      (SELECT COUNT(*) FROM prices WHERE product_id=p.id) checks
      FROM products p ORDER BY p.id DESC''').fetchall()
    result=[]
    for p in products:
        d=dict(p)
        d['history']=[dict(x) for x in con.execute('SELECT price,checked_at FROM prices WHERE product_id=? ORDER BY id DESC LIMIT 12',(p['id'],)).fetchall()]
        result.append(d)
    con.close()
    return result

def load_settings():
    try: return json.loads(SETTINGS.read_text(encoding='utf-8'))
    except Exception: return {}


def save_settings(data):
    SETTINGS.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def apish_request(path, params=None):
    key = load_settings().get('apish_key', '').strip()
    if not key: raise ValueError('API.SH 키를 먼저 저장해 주세요.')
    url = 'https://api.api.sh' + path
    if params: url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'x-api-key': key, 'Accept': 'application/json', 'User-Agent': 'PriceTrackerMVP/2.0'})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        raise ValueError(f'API.SH 오류 ({e.code}): {detail}')
    except Exception as e: raise ValueError(f'API 연결 실패: {e}')
    if isinstance(data, dict) and data.get('success') is False: raise ValueError(data.get('error', 'API 오류'))
    return data


def search_11st(keyword):
    data = apish_request('/11st/v1/search', {'keyword': keyword, 'page': 1, 'sort': 'ranking'})
    return data.get('items', []) if isinstance(data, dict) else []


def check_auto_products():
    con = db()
    due = con.execute("SELECT id,external_id FROM products WHERE provider='apish11st' AND (last_checked='' OR last_checked < ?)", ((datetime.now()-timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S'),)).fetchall()
    con.close()
    for item in due:
        con = db()
        try:
            data = apish_request('/11st/v1/product', {'id': item['external_id']})
            price = int(data.get('price') or 0)
            if price <= 0: raise ValueError('API 응답에 유효한 가격이 없습니다.')
            add_price(con, item['id'], price)
            con.execute("UPDATE products SET last_checked=?,last_error='' WHERE id=?",(now(),item['id']))
            con.commit()
        except Exception as e:
            con.execute('UPDATE products SET last_checked=?,last_error=? WHERE id=?',(now(),str(e)[:250],item['id']))
            con.commit()
        finally: con.close()



def safe_public_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('http 또는 https 상품 페이지 주소를 입력해 주세요.')
    host = parsed.hostname.lower()
    if host in ('localhost',) or host.endswith('.local'):
        raise ValueError('공개 웹사이트 주소만 조회할 수 있습니다.')
    try:
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global:
                raise ValueError('공개 웹사이트 주소만 조회할 수 있습니다.')
    except socket.gaierror:
        raise ValueError('상품 페이지 주소의 도메인을 확인할 수 없습니다.')
    return url

def price_from_jsonld(obj):
    found=[]
    def walk(x):
        if isinstance(x, dict):
            typ=x.get('@type', [])
            if isinstance(typ,str): typ=[typ]
            if any(t.lower() in ('product','offer','aggregateoffer') for t in typ):
                offers=x.get('offers', x)
                candidates=offers if isinstance(offers,list) else [offers]
                for offer in candidates:
                    if isinstance(offer,dict):
                        for key in ('price','lowPrice'):
                            val=offer.get(key)
                            cur=str(offer.get('priceCurrency','KRW')).upper()
                            if val is not None and cur in ('KRW','WON'):
                                try:
                                    n=int(float(str(val).replace(',','')))
                                    if n>0: found.append(n)
                                except: pass
            for v in x.values(): walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    walk(obj)
    return min(found) if found else None

def fetch_public_price(url):
    url=safe_public_url(url)
    req=urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 (compatible; PriceTrackerMVP/3.0; +https://example.invalid/bot)','Accept':'text/html,application/xhtml+xml'})
    try:
        with urllib.request.urlopen(req,timeout=18) as resp:
            if resp.status != 200: raise ValueError(f'상품 페이지 응답 오류: HTTP {resp.status}')
            raw=resp.read(2_000_000)
            charset=resp.headers.get_content_charset() or 'utf-8'
    except urllib.error.HTTPError as e:
        raise ValueError(f'상품 페이지 접근 실패: HTTP {e.code}')
    except Exception as e:
        if isinstance(e,ValueError): raise
        raise ValueError(f'상품 페이지를 읽지 못했습니다: {e}')
    html=raw.decode(charset,'replace')
    for m in re.finditer(r'<script[^>]+type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script\s*>',html,re.I|re.S):
        txt=m.group(1).strip()
        try:
            val=json.loads(txt)
            price=price_from_jsonld(val)
            if price: return price
        except Exception: pass
    # 일부 쇼핑몰은 JSON-LD 없이 itemprop만 제공합니다.
    patterns=[r'<meta[^>]+itemprop=[\"\']price[\"\'][^>]+content=[\"\']([0-9,.]+)',r'<meta[^>]+content=[\"\']([0-9,.]+)[\"\'][^>]+itemprop=[\"\']price']
    for pat in patterns:
        m=re.search(pat,html,re.I)
        if m:
            try:
                n=int(float(m.group(1).replace(',','')))
                if n>0:return n
            except: pass
    raise ValueError('페이지에서 공개 가격 정보를 찾지 못했습니다. 옵션 선택·로그인·자바스크립트가 필요한 상품일 수 있습니다.')

def check_url_products():
    con=db()
    due=con.execute("SELECT id,url FROM products WHERE provider='public_url' AND url<>'' AND (last_checked='' OR last_checked < ?)",( (datetime.now()-timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S'),)).fetchall()
    con.close()
    for item in due:
        con=db()
        try:
            price=fetch_public_price(item['url'])
            add_price(con, item['id'], price)
            con.execute("UPDATE products SET last_checked=?,last_error='' WHERE id=?",(now(),item['id']))
            con.commit()
        except Exception as e:
            con.execute('UPDATE products SET last_checked=?,last_error=? WHERE id=?',(now(),str(e)[:250],item['id']))
            con.commit()
        finally: con.close()


def scheduler_loop():
    while True:
        try: check_auto_products(); check_url_products()
        except Exception: pass
        time.sleep(300)


HTML = r'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>가격 추적기</title>
<style>
:root{font-family:system-ui,-apple-system,'Malgun Gothic',sans-serif;color:#172033;background:#f4f6fa}*{box-sizing:border-box}body{margin:0}.wrap{max-width:1100px;margin:32px auto;padding:0 18px}.hero{background:linear-gradient(120deg,#172554,#2563eb);color:white;padding:28px;border-radius:18px}.hero h1{margin:0 0 8px;font-size:27px}.hero p{margin:0;color:#dbeafe}.panel{background:white;border:1px solid #e4e8f0;border-radius:14px;padding:20px;margin-top:18px;box-shadow:0 3px 14px #1725540a}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.field label{display:block;font-size:12px;color:#64748b;margin-bottom:6px}.field input,.field select{width:100%;padding:11px;border:1px solid #d8deea;border-radius:9px;font:inherit}.btn{border:0;border-radius:9px;padding:11px 16px;background:#2563eb;color:white;font-weight:700;cursor:pointer}.btn.secondary{background:#e9efff;color:#1d4ed8}.btn.danger{background:#fee2e2;color:#991b1b}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.muted{color:#64748b;font-size:13px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:18px}.stat{background:white;border:1px solid #e4e8f0;border-radius:12px;padding:16px}.stat b{font-size:24px;display:block;margin-top:5px}.product{border:1px solid #e4e8f0;border-radius:12px;padding:16px;margin-top:12px}.product h3{margin:0 0 8px}.row{display:flex;justify-content:space-between;gap:12px;align-items:center}.price{font-size:22px;font-weight:800}.tag{font-size:12px;border-radius:20px;padding:4px 9px;background:#eff6ff;color:#1d4ed8}.tag.alert{background:#dcfce7;color:#166534}.history{margin-top:12px;border-top:1px solid #edf0f5;padding-top:10px;font-size:13px;color:#64748b}.history span{display:inline-block;margin:3px 8px 3px 0}.empty{text-align:center;padding:35px;color:#64748b}.note{background:#fffbeb;border:1px solid #fde68a;color:#854d0e;padding:13px;border-radius:10px;font-size:13px;margin-top:14px}a{color:#2563eb;word-break:break-all}@media(max-width:760px){.grid{grid-template-columns:1fr 1fr}.cards{grid-template-columns:1fr}.row{align-items:flex-start;flex-direction:column}}
</style></head><body><div class="wrap"><header class="hero"><h1>가격 추적기 · 7차 버전</h1><p>구글 쇼핑에서 상품을 찾고 공개된 상품 페이지 가격을 자동 추적합니다.</p></header>
<div class="actions"><button class="btn secondary" onclick="enableNotifications()">가격 알림 켜기</button><span id="notifstatus" class="muted">브라우저 알림은 이 화면이 열려 있을 때 표시됩니다.</span></div><div class="cards"><div class="stat"><span class="muted">추적 상품</span><b id="total">0</b></div><div class="stat"><span class="muted">가격 기록</span><b id="checks">0</b></div><div class="stat"><span class="muted">목표가 도달</span><b id="alerts">0</b></div></div>
<section class="panel"><h2 style="margin-top:0">통합 상품 검색 및 가격 비교</h2><div class="grid" style="grid-template-columns:2fr 1fr"><div class="field"><label>검색할 상품명</label><input id="googlekw" placeholder="예: 로지텍 MX Master 3S"></div><div class="field" style="align-self:end"><button class="btn" onclick="searchAllShops()">쇼핑몰 검색 열기</button></div></div><div class="actions" style="margin-top:10px"><button class="btn secondary" onclick="openShopSearch('google')">Google 쇼핑</button><button class="btn secondary" onclick="openShopSearch('naver')">네이버쇼핑</button><button class="btn secondary" onclick="openShopSearch('coupang')">쿠팡</button><button class="btn secondary" onclick="openShopSearch('gmarket')">G마켓</button><button class="btn secondary" onclick="openShopSearch('11st')">11번가</button></div><div class="actions" style="margin-top:10px"><button class="btn secondary" onclick="pasteProductLink()">복사한 상품 링크 가져오기</button><span id="pastehint" class="muted">검색 결과에서 상품을 고른 뒤 판매 페이지 링크를 복사하고 이 버튼을 누르세요.</span></div><div class="muted" style="margin-top:8px">검색 결과를 무단으로 자동 수집하지 않습니다. 구글 쇼핑에서 판매처 상품을 선택한 뒤 링크를 복사하면 주소 입력란에 자동으로 들어갑니다. 검색 API 비용은 없으며, 가격 추적 등록 시 판매 페이지의 공개 가격 정보를 확인합니다.</div><div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-top:14px"><div class="field"><label>상품명</label><input id="trackname" placeholder="예: 로지텍 MX Master 3S"></div><div class="field"><label>모델명 / 품번</label><input id="trackmodel" placeholder="예: MRSD3"></div><div class="field"><label>옵션 / 구성</label><input id="trackvariant" placeholder="예: 블랙 / 정품"></div><div class="field"><label>상품 페이지 URL</label><input id="trackurl" type="url" placeholder="https://판매처의-상품-주소"></div><div class="field"><label>배송비 (원)</label><input id="trackshipping" type="number" min="0" value="0"></div><div class="field"><label>비교 그룹명 (선택)</label><input id="trackgroup" placeholder="같은 상품끼리 묶을 이름"></div><div class="field"><label>목표 가격 (원)</label><input id="tracktarget" type="number" min="0" placeholder="예: 50000"></div><div class="field" style="align-self:end"><button class="btn secondary" onclick="trackUrl()">URL 가격 추적 등록</button></div></div><div id="urlstatus" class="muted" style="margin-top:8px"></div></section>
<section class="panel"><h2 style="margin-top:0">추가 자동 수집 연결 · 11번가</h2><div class="grid" style="grid-template-columns:2fr 1fr"><div class="field"><label>API.SH API 키</label><input id="apishkey" type="password" autocomplete="off" placeholder="API.SH에서 발급받은 키"></div><div class="field" style="align-self:end"><button class="btn" onclick="saveKey()">키 저장</button></div></div><div class="muted" style="margin-top:8px">API.SH의 현재 무료 제공량과 요금은 제공업체 정책에 따라 달라질 수 있습니다. 사용 전에 요금표를 확인하세요. 키는 이 PC에 저장됩니다.</div><div class="grid" style="grid-template-columns:2fr 1fr;margin-top:14px"><div class="field"><label>11번가 상품 검색</label><input id="kw" placeholder="예: 로지텍 MX Master 3S"></div><div class="field" style="align-self:end"><button class="btn secondary" onclick="search11st()">상품 검색</button></div></div><div id="searchresults"></div></section>
<section class="panel"><h2 style="margin-top:0">상품 등록 (직접 입력)</h2><form id="add"><div class="grid"><div class="field"><label>상품명 *</label><input name="name" required placeholder="예: 무선 마우스"></div><div class="field"><label>쇼핑몰</label><select name="shop"><option>직접 입력</option><option>쿠팡</option><option>네이버쇼핑</option><option>11번가</option><option>G마켓</option><option>기타</option></select></div><div class="field"><label>상품 페이지 주소</label><input name="url" type="url" placeholder="https://..."></div><div class="field"><label>모델명 / 품번</label><input name="model_no" placeholder="예: MRSD3"></div><div class="field"><label>옵션 / 구성</label><input name="variant" placeholder="예: 블랙 / 정품"></div><div class="field"><label>비교 그룹명</label><input name="compare_key" placeholder="같은 상품끼리 묶을 이름"></div><div class="field"><label>배송비 (원)</label><input name="shipping_price" type="number" min="0" value="0"></div><div class="field"><label>목표 가격 (원)</label><input name="target_price" type="number" min="0" placeholder="예: 50000"></div></div><div class="actions"><button class="btn">상품 등록</button></div></form><div class="note">Google 쇼핑에서 상품을 찾은 뒤 판매 페이지 URL을 등록하면, 페이지에 공개된 JSON-LD 가격 정보를 하루 1회 확인합니다. 사이트가 가격 정보를 숨기거나 차단하면 자동 확인이 실패할 수 있습니다. 11번가 API 연동도 선택적으로 유지됩니다.</div></section>
<section class="panel"><div class="row"><div><h2 style="margin:0">내 상품</h2><div class="muted">모델명·옵션·비교 그룹을 구분하고 배송비 포함 가격과 목표가 알림을 관리합니다.</div></div><div class="actions" style="margin:0"><button class="btn secondary" onclick="exportCsv()">CSV 내보내기</button><button class="btn secondary" onclick="load()">새로고침</button></div></div><h3 style="margin:18px 0 8px">동일 상품 판매처 비교</h3><div class="muted">비교 그룹명 → 모델명/옵션 → 상품명 순으로 묶습니다. 같은 모델·옵션인지 확인한 뒤 동일한 비교 그룹명을 입력하세요.</div><div id="comparison"></div><div id="items"></div></section>
<p class="muted" style="text-align:center;margin:20px">추적 기록과 API 키는 이 PC에 저장됩니다. Google 쇼핑 검색은 구글 페이지에서 직접 수행하며, 등록한 상품 URL은 가격 확인을 위해 해당 판매처에 요청됩니다.</p></div>
<script>
const won=n=>n==null?'—':new Intl.NumberFormat('ko-KR').format(n)+'원';
function searchQuery(){let q=document.querySelector('#googlekw').value.trim();if(!q){alert('검색할 상품명을 입력해 주세요.');return null}return q}
function openShopSearch(shop){let q=searchQuery();if(!q)return;let urls={google:'https://www.google.com/search?tbm=shop&q=',naver:'https://search.shopping.naver.com/search/all?query=',coupang:'https://www.coupang.com/np/search?q=',gmarket:'https://browse.gmarket.co.kr/search?keyword=', '11st':'https://search.11st.co.kr/Search.tmall?kwd='};window.open(urls[shop]+encodeURIComponent(q),'_blank','noopener')}
function searchAllShops(){let q=searchQuery();if(!q)return;['google','naver','coupang','gmarket','11st'].forEach((shop,i)=>setTimeout(()=>openShopSearchFromQuery(shop,q),i*180))}
function openShopSearchFromQuery(shop,q){let urls={google:'https://www.google.com/search?tbm=shop&q=',naver:'https://search.shopping.naver.com/search/all?query=',coupang:'https://www.coupang.com/np/search?q=',gmarket:'https://browse.gmarket.co.kr/search?keyword=', '11st':'https://search.11st.co.kr/Search.tmall?kwd='};window.open(urls[shop]+encodeURIComponent(q),'_blank','noopener')}
async function pasteProductLink(){let hint=document.querySelector('#pastehint');try{let url=(await navigator.clipboard.readText()).trim();if(!/^https?:\/\//i.test(url))throw Error('클립보드에 http 또는 https 상품 링크가 없습니다.');document.querySelector('#trackurl').value=url;hint.textContent='상품 링크를 가져왔습니다. 상품명과 목표 가격을 확인한 뒤 URL 가격 추적 등록을 누르세요.';document.querySelector('#trackname').focus()}catch(e){hint.textContent='클립보드 접근이 차단되었습니다. 주소 입력란에 링크를 직접 붙여 넣어 주세요.';alert('링크를 가져오지 못했습니다. 브라우저의 클립보드 권한을 허용하거나 직접 붙여 넣어 주세요.')}}
async function trackUrl(){let url=document.querySelector('#trackurl').value.trim(),target=document.querySelector('#tracktarget').value,name=document.querySelector('#trackname').value.trim(),shipping=document.querySelector('#trackshipping').value,model_no=document.querySelector('#trackmodel').value.trim(),variant=document.querySelector('#trackvariant').value.trim(),compare_key=document.querySelector('#trackgroup').value.trim();let box=document.querySelector('#urlstatus');if(!url){alert('상품 페이지 주소를 입력해 주세요.');return}box.textContent='상품 페이지에서 가격 정보를 확인하는 중...';try{let r=await api('/api/trackurl',{url,target_price:target,name,shipping_price:shipping,model_no,variant,compare_key});box.textContent='등록 완료 · 첫 가격 '+won(r.price)+' · 24시간마다 자동 확인';document.querySelector('#trackurl').value='';document.querySelector('#tracktarget').value='';document.querySelector('#trackname').value='';document.querySelector('#trackshipping').value='0';document.querySelector('#trackmodel').value='';document.querySelector('#trackvariant').value='';document.querySelector('#trackgroup').value='';load()}catch(e){box.textContent=e.message}}
async function checkUrlNow(id){try{let r=await api('/api/checkurl',{id});alert('현재 확인 가격: '+won(r.price));load()}catch(e){alert(e.message)}}
async function saveKey(){let key=document.querySelector('#apishkey').value.trim();if(!key){alert('API 키를 입력해 주세요.');return}try{await api('/api/settings',{apish_key:key});document.querySelector('#apishkey').value='';alert('API 키를 이 PC에 저장했습니다.');}catch(e){alert(e.message)}}
async function search11st(){let q=document.querySelector('#kw').value.trim();if(!q){alert('검색어를 입력해 주세요.');return}let box=document.querySelector('#searchresults');box.textContent='검색 중...';try{let d=await api('/api/search11st',{keyword:q});d.items.sort((a,b)=>(Number(a.price)||Number.MAX_SAFE_INTEGER)-(Number(b.price)||Number.MAX_SAFE_INTEGER));box.innerHTML=d.items.length?d.items.map(x=>`<div class="product"><div class="row"><div><b>${esc(x.name)}</b><div class="muted">${esc(x.brand||'')} · 상품번호 ${esc(x.id)}</div><a href="${esc(x.url||'')}" target="_blank" rel="noopener">11번가 상품 보기</a></div><div><b>${won(x.price)}</b><br><button class="btn" onclick="track11st('${esc(x.id)}')">이 상품 추적</button></div></div></div>`).join(''):'검색 결과가 없습니다.'}catch(e){box.textContent=e.message}}
async function track11st(id){try{await api('/api/track11st',{id});alert('자동 추적 상품으로 등록했습니다.');load()}catch(e){alert(e.message)}}
async function api(path,body){let r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});let j=await r.json();if(!r.ok)throw Error(j.error||'오류');return j}
async function load(){let data=await api('/api/products');let total=0,checks=0,alerts=0;data.forEach(p=>{total++;checks+=p.checks; if(p.target_price&&p.current_price&&p.current_price<=p.target_price)alerts++});document.querySelector('#total').textContent=total;document.querySelector('#checks').textContent=checks;document.querySelector('#alerts').textContent=alerts;let root=document.querySelector('#items'),compare=document.querySelector('#comparison');if(!data.length){root.innerHTML='<div class="empty">아직 등록된 상품이 없습니다. 위에서 첫 상품을 등록해 주세요.</div>';compare.innerHTML='';return}
const groups={};data.forEach(p=>{let raw=p.compare_key||[p.model_no,p.variant].filter(Boolean).join('|')||p.name;let key=String(raw||'').toLocaleLowerCase('ko-KR').replace(/[\s\-_.()[\]]/g,'');(groups[key]??=[]).push(p)});
let comparable=Object.values(groups).filter(g=>g.length>1).map(g=>g.sort((a,b)=>((a.current_price??Number.MAX_SAFE_INTEGER)+(a.shipping_price||0))-((b.current_price??Number.MAX_SAFE_INTEGER)+(b.shipping_price||0))));
compare.innerHTML=comparable.length?comparable.map(g=>`<article class="product"><b>${esc(g[0].name)}</b><div class="history">${g.map((p,i)=>`<div class="row" style="padding:7px 0"><div>${i===0?'<span class="tag alert">배송포함 최저가</span> ':''}<b>${esc(p.shop)}</b> · 상품 ${won(p.current_price)} + 배송 ${won(p.shipping_price||0)}</div><b>${won(p.current_price==null?null:p.current_price+(p.shipping_price||0))}</b></div>`).join('')}</div></article>`).join(''):'<div class="empty">같은 상품명으로 2개 이상의 판매처를 등록하면 배송비 포함 비교가 여기에 표시됩니다.</div>';
root.innerHTML=data.map(p=>{let hit=p.target_price&&p.current_price&&p.current_price+(p.shipping_price||0)<=p.target_price;return `<article class="product"><div class="row"><div><h3>${esc(p.name)} <span class="tag">${esc(p.shop)}</span> ${p.provider==='apish11st'?'<span class="tag alert">11번가 자동추적</span>':p.provider==='public_url'?'<span class="tag alert">페이지 가격 자동추적</span>':''} ${hit?'<span class="tag alert">배송포함 목표가 도달</span>':''}</h3><div class="muted">${p.url?`<a href="${esc(p.url)}" target="_blank" rel="noopener">상품 페이지 열기</a>`:'상품 주소 미등록'} · 등록 ${esc(p.created_at)}</div></div><div class="price">${won(p.current_price==null?null:p.current_price+(p.shipping_price||0))}</div></div><div class="row" style="margin-top:10px"><div class="muted">모델: <b>${esc(p.model_no||'—')}</b> · 옵션: <b>${esc(p.variant||'—')}</b> · 비교 그룹: <b>${esc(p.compare_key||'자동')}</b><br>상품가격: <b>${won(p.current_price)}</b> · 배송비: <b>${won(p.shipping_price||0)}</b> · 최저 기록: <b>${won(p.lowest_price)}</b> · 목표가: <b>${won(p.target_price)}</b> · 기록 ${p.checks}회</div><div class="actions" style="margin:0"><button class="btn secondary" onclick="record(${p.id})">가격 기록</button>${p.provider==='public_url'?`<button class="btn secondary" onclick="checkUrlNow(${p.id})">지금 가격 확인</button>`:''}<button class="btn danger" onclick="removeItem(${p.id})">삭제</button></div></div>${p.last_error?`<div class="muted" style="color:#b91c1c">자동 수집 오류: ${esc(p.last_error)}</div>`:''}<div class="history">최근 기록: ${p.history.length?p.history.map(x=>`<span>${won(x.price)} · ${esc(x.checked_at)}</span>`).join(''):'없음'}</div></article>`}).join('')}

function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
document.querySelector('#add').addEventListener('submit',async e=>{e.preventDefault();let f=new FormData(e.target);try{await api('/api/products',{name:f.get('name'),shop:f.get('shop'),url:f.get('url'),target_price:f.get('target_price'),shipping_price:f.get('shipping_price'),model_no:f.get('model_no'),variant:f.get('variant'),compare_key:f.get('compare_key')});e.target.reset();load()}catch(x){alert(x.message)}});
async function record(id){let v=prompt('현재 판매 가격을 원 단위 숫자로 입력하세요.');if(v===null)return;if(!/^\d+$/.test(v)){alert('가격은 숫자로 입력해 주세요.');return}try{let r=await api('/api/price',{id,price:Number(v)});if(r.alert)alert('목표 가격에 도달했습니다!');load()}catch(e){alert(e.message)}}
async function removeItem(id){if(!confirm('이 상품과 가격 기록을 삭제할까요?'))return;await api('/api/delete',{id});load()}
async function enableNotifications(){if(!('Notification' in window)){document.querySelector('#notifstatus').textContent='이 브라우저는 알림을 지원하지 않습니다.';return}let p=await Notification.requestPermission();document.querySelector('#notifstatus').textContent=p==='granted'?'가격 알림이 켜졌습니다.':'알림 권한이 허용되지 않았습니다.';if(p==='granted')pollAlerts()}
async function pollAlerts(){try{let d=await api('/api/alerts');if(d.items.length&&'Notification'in window&&Notification.permission==='granted'){d.items.forEach(a=>new Notification('가격 목표가 도달',{body:a.message}));await api('/api/alerts/ack',{ids:d.items.map(a=>a.id)})}}catch(e){}}
function exportCsv(){location.href='/export.csv'}load();setInterval(load,60000);setInterval(pollAlerts,30000);
</script></body></html>'''

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def send(self, code, data, ctype='application/json; charset=utf-8'):
        b=data.encode('utf-8') if isinstance(data,str) else json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(code); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def body(self):
        n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/': return self.send(200,HTML,'text/html; charset=utf-8')
        if path=='/api/products': return self.send(200,json.dumps(rows(),ensure_ascii=False))
        if path=='/api/connector/status': return self.send(200,{'apish_configured':bool(load_settings().get('apish_key'))})
        if path=='/api/alerts':
            con=db(); items=[dict(x) for x in con.execute('SELECT id,product_id,message,created_at FROM alert_events WHERE acknowledged=0 ORDER BY id').fetchall()]; con.close(); return self.send(200,{'items':items})
        if path=='/export.csv':
            con=db(); out=io.StringIO(); w=csv.writer(out); w.writerow(['상품명','모델명','옵션','비교그룹','쇼핑몰','상품주소','상품가격','배송비','배송포함가격','목표가격','최저기록','가격기록수','등록일'])
            for p in rows(): w.writerow([p['name'],p.get('model_no',''),p.get('variant',''),p.get('compare_key',''),p['shop'],p['url'],p['current_price'],p.get('shipping_price',0),(p['current_price'] or 0)+(p.get('shipping_price',0) or 0),p['target_price'],p['lowest_price'],p['checks'],p['created_at']])
            con.close(); return self.send(200,'\ufeff'+out.getvalue(),'text/csv; charset=utf-8')
        self.send(404,{'error':'Not found'})
    def do_POST(self):
        path=urlparse(self.path).path
        try:
            d=self.body(); con=db()
            if path=='/api/alerts/ack':
                ids=d.get('ids') or []; ids=[int(x) for x in ids if str(x).isdigit()]
                if ids: con.executemany('UPDATE alert_events SET acknowledged=1 WHERE id=?', [(x,) for x in ids])
                con.commit(); con.close(); return self.send(200,{'ok':True})
            if path=='/api/settings':
                key=str(d.get('apish_key','')).strip()
                if len(key)<8: raise ValueError('API 키 형식을 확인해 주세요.')
                save_settings({'apish_key':key}); con.close(); return self.send(200,{'ok':True})
            if path=='/api/trackurl':
                url=safe_public_url(str(d.get('url','')).strip())
                price=fetch_public_price(url)
                target=d.get('target_price'); target=int(target) if str(target or '').strip() else None
                shipping=int(d.get('shipping_price') or 0)
                if target is not None and target<0: raise ValueError('목표 가격은 0 이상이어야 합니다.')
                if shipping<0: raise ValueError('배송비는 0 이상이어야 합니다.')
                host=urllib.parse.urlparse(url).netloc
                name=str(d.get('name') or host+' 상품')
                cur=con.execute('INSERT INTO products(name,shop,url,target_price,created_at,provider,last_checked,shipping_price,model_no,variant,compare_key) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(name,host,url,target,now(),'public_url',now(),shipping,str(d.get('model_no') or '').strip(),str(d.get('variant') or '').strip(),str(d.get('compare_key') or '').strip()))
                add_price(con, cur.lastrowid, price)
                con.commit(); con.close(); return self.send(200,{'ok':True,'price':price})
            if path=='/api/checkurl':
                pid=int(d.get('id')); p=con.execute("SELECT url FROM products WHERE id=? AND provider='public_url'",(pid,)).fetchone()
                if not p: raise ValueError('자동 추적 URL 상품을 찾을 수 없습니다.')
                price=fetch_public_price(p['url'])
                add_price(con, pid, price)
                con.execute("UPDATE products SET last_checked=?,last_error='' WHERE id=?",(now(),pid))
                con.commit(); con.close(); return self.send(200,{'ok':True,'price':price})
            if path=='/api/search11st':
                keyword=str(d.get('keyword','')).strip()
                if not keyword: raise ValueError('검색어를 입력해 주세요.')
                items=search_11st(keyword)[:20]
                con.close(); return self.send(200,{'items':items})
            if path=='/api/track11st':
                ext=str(d.get('id','')).strip()
                if not ext.isdigit(): raise ValueError('상품 번호가 올바르지 않습니다.')
                data=apish_request('/11st/v1/product',{'id':ext})
                price=int(data.get('price') or 0)
                if price<=0: raise ValueError('상품 가격을 확인할 수 없습니다.')
                name=str(data.get('name') or '11번가 상품')
                url=str(data.get('url') or '')
                cur=con.execute('INSERT INTO products(name,shop,url,target_price,created_at,provider,external_id,last_checked) VALUES(?,?,?,?,?,?,?,?)',(name,'11번가',url,None,now(),'apish11st',ext,now()))
                add_price(con, cur.lastrowid, price)
                con.commit(); con.close(); return self.send(200,{'ok':True,'price':price})
            if path=='/api/products':
                name=str(d.get('name','')).strip()
                if not name: raise ValueError('상품명을 입력해 주세요.')
                target=d.get('target_price'); target=int(target) if str(target or '').strip() else None
                shipping=int(d.get('shipping_price') or 0)
                if target is not None and target<0: raise ValueError('목표 가격은 0 이상이어야 합니다.')
                if shipping<0: raise ValueError('배송비는 0 이상이어야 합니다.')
                con.execute('INSERT INTO products(name,shop,url,target_price,created_at,shipping_price,model_no,variant,compare_key) VALUES(?,?,?,?,?,?,?,?,?)',(name,str(d.get('shop') or '직접 입력'),str(d.get('url') or '').strip(),target,now(),shipping,str(d.get('model_no') or '').strip(),str(d.get('variant') or '').strip(),str(d.get('compare_key') or '').strip())); con.commit(); con.close(); return self.send(200,{'ok':True})
            if path=='/api/price':
                pid=int(d.get('id')); price=int(d.get('price'))
                if price<=0: raise ValueError('가격은 1원 이상이어야 합니다.')
                p=con.execute('SELECT target_price FROM products WHERE id=?',(pid,)).fetchone()
                if not p: raise ValueError('상품을 찾을 수 없습니다.')
                alert=add_price(con, pid, price); con.commit(); con.close(); return self.send(200,{'ok':True,'alert':alert})
            if path=='/api/delete':
                pid=int(d.get('id')); con.execute('DELETE FROM prices WHERE product_id=?',(pid,)); con.execute('DELETE FROM products WHERE id=?',(pid,)); con.commit(); con.close(); return self.send(200,{'ok':True})
            con.close(); self.send(404,{'error':'Not found'})
        except Exception as e:
            try: con.close()
            except: pass
            self.send(400,{'error':str(e)})

if __name__=='__main__':
    db().close()
    server=ThreadingHTTPServer((HOST,PORT),Handler)
    threading.Thread(target=scheduler_loop,daemon=True).start()
    print('가격 추적기가 실행 중입니다: http://127.0.0.1:8765')
    print('종료하려면 이 창에서 Ctrl+C를 누르세요.')
    threading.Timer(1.0,lambda:webbrowser.open(f'http://{HOST}:{PORT}')).start()
    try: server.serve_forever()
    except KeyboardInterrupt: print('\n프로그램을 종료합니다.')
    finally: server.server_close()

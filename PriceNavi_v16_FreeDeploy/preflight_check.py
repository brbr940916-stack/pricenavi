#!/usr/bin/env python3
"""Production configuration preflight for Price Tracker Cloud."""
import os, re, sys
from pathlib import Path

def load_env(path: Path):
    values = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values

p = Path(sys.argv[1] if len(sys.argv) > 1 else '.env')
if not p.is_file():
    print(f'FAIL: {p} 파일이 없습니다. .env.example을 복사해 .env를 만드세요.')
    raise SystemExit(2)
env = load_env(p)
errors, warns = [], []
secret = env.get('APP_SECRET', '')
if len(secret) < 32 or 'CHANGE_THIS' in secret or 'example' in secret.lower():
    errors.append('APP_SECRET은 예측 불가능한 임의 문자열 32자 이상이어야 합니다.')
domain = env.get('DOMAIN', '')
if not domain or domain == 'price.example.com' or ' ' in domain or '/' in domain:
    errors.append('DOMAIN을 실제 서비스 도메인(예: tracker.example.kr)으로 설정하세요.')
base = env.get('PUBLIC_BASE_URL', '')
if not base.startswith('https://') or 'example.com' in base or base.rstrip('/') != f'https://{domain}':
    errors.append('PUBLIC_BASE_URL은 https://실제DOMAIN 과 정확히 일치해야 합니다.')
if env.get('COOKIE_SECURE', 'true').lower() != 'true':
    errors.append('운영 환경에서는 COOKIE_SECURE=true여야 합니다.')
if env.get('TOSS_CLIENT_KEY') and not env.get('TOSS_SECRET_KEY'):
    errors.append('TOSS_CLIENT_KEY를 설정했다면 TOSS_SECRET_KEY도 설정해야 합니다.')
if env.get('TOSS_SECRET_KEY') and not env.get('TOSS_CLIENT_KEY'):
    errors.append('TOSS_SECRET_KEY를 설정했다면 TOSS_CLIENT_KEY도 설정해야 합니다.')
if not env.get('SMTP_HOST') or 'example.com' in env.get('SMTP_HOST', ''):
    warns.append('SMTP 미설정: 이메일 인증/비밀번호 재설정/가격 알림 메일이 동작하지 않을 수 있습니다.')
if not env.get('ADMIN_EMAIL') or 'example.com' in env.get('ADMIN_EMAIL', ''):
    warns.append('ADMIN_EMAIL을 실제 관리자 이메일로 변경하세요.')
if not env.get('TOSS_CLIENT_KEY'):
    warns.append('Toss 키 미설정: 유료 결제 기능은 비활성 상태입니다.')
for w in warns: print('WARN:', w)
for e in errors: print('FAIL:', e)
if errors:
    print(f'\n사전 점검 실패: 오류 {len(errors)}개, 경고 {len(warns)}개')
    raise SystemExit(1)
print(f'\n사전 점검 통과: 오류 0개, 경고 {len(warns)}개')

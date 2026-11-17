#!/usr/bin/env python3
"""Pre-deployment checks for the Price Tracker VPS package (stdlib only)."""
from pathlib import Path
from urllib.parse import urlparse
import sys

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"

def read_env(path):
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip("\"'")
    return values

errors, warnings = [], []
if not ENV.is_file():
    errors.append(".env 파일이 없습니다. cp .env.example .env 로 생성하세요.")
    env = {}
else:
    env = read_env(ENV)
secret = env.get("APP_SECRET", "")
if len(secret) < 32 or "CHANGE_THIS" in secret or "example" in secret.lower():
    errors.append("APP_SECRET은 32자 이상의 예측 불가능한 임의 문자열이어야 합니다.")
domain = env.get("DOMAIN", "")
if not domain or "example" in domain.lower() or " " in domain:
    errors.append("DOMAIN을 실제 서비스 도메인으로 바꾸세요.")
base = env.get("PUBLIC_BASE_URL", "")
u = urlparse(base)
if u.scheme != "https" or not u.hostname:
    errors.append("PUBLIC_BASE_URL은 https://실제도메인 형식이어야 합니다.")
elif domain and u.hostname.lower() != domain.lower():
    errors.append("PUBLIC_BASE_URL의 호스트와 DOMAIN이 일치하지 않습니다.")
if env.get("COOKIE_SECURE", "true").lower() != "true":
    errors.append("운영 HTTPS 환경에서는 COOKIE_SECURE=true를 권장합니다.")
if bool(env.get("TOSS_CLIENT_KEY")) != bool(env.get("TOSS_SECRET_KEY")):
    errors.append("TOSS_CLIENT_KEY와 TOSS_SECRET_KEY는 둘 다 설정하거나 둘 다 비워 두세요.")
if not env.get("SMTP_HOST"):
    warnings.append("SMTP_HOST가 비어 있어 이메일 인증/알림 메일은 발송되지 않습니다.")
if not env.get("TOSS_CLIENT_KEY"):
    warnings.append("토스 키가 없어 결제 기능은 비활성 상태입니다.")
for w in warnings: print("[주의]", w)
for e in errors: print("[오류]", e)
if errors:
    print(f"배포 점검 실패: {len(errors)}개 오류")
    sys.exit(1)
print("배포 사전 점검 통과. 실제 DNS/방화벽/HTTPS/결제/메일은 별도 실환경 확인이 필요합니다.")

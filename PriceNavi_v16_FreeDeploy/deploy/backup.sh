#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
# app 컨테이너의 비특권 사용자(UID 10001)가 쓸 수 있도록 디렉터리 권한 설정
if [ "$(id -u)" = "0" ]; then chown 10001:10001 backups; chmod 0750 backups; else chmod 0770 backups 2>/dev/null || true; fi
docker compose exec -T app python /app/backup_db.py
# 백업은 별도 서버/오브젝트 스토리지에도 복사해야 장애·서버 분실에 대비할 수 있습니다.

가격 추적기 v15
===============

v14의 PWA 기능 및 기존 회원/상품/가격/결제 코드를 유지하면서 서버 배포 안정화 자료를 보완한 버전입니다.

주요 추가 사항
- 배포 안내서의 비밀키 환경변수명을 실제 코드와 일치하도록 APP_SECRET으로 수정했습니다.
- deploy/preflight.py: .env의 APP_SECRET, DOMAIN, PUBLIC_BASE_URL, COOKIE_SECURE, Toss 키 구성을 점검합니다.
- Docker 배포의 SQLite 백업 경로를 /data/backups로 지정했습니다.
- deploy/backup.sh: 앱 컨테이너에서 SQLite 온라인 백업을 만들고 호스트 backups/ 폴더로 복사합니다.
- deploy/price-tracker-backup.service 및 .timer: 매일 03:15 백업 실행 예시입니다. 설치 경로를 실제 위치로 수정한 후 systemd에 등록해야 합니다.
- deploy/RESTORE_GUIDE_KO.txt: 백업 복구 절차를 설명합니다.

빠른 점검
1. .env.example을 .env로 복사합니다.
2. APP_SECRET을 32자 이상의 임의 비밀 문자열로 바꾸고 DOMAIN/PUBLIC_BASE_URL을 실제 도메인으로 수정합니다.
3. python3 deploy/preflight.py
4. docker compose up -d --build

주의
- 실제 VPS 배포, DNS, 방화벽, HTTPS 인증서, Docker 이미지 빌드 및 Android/iPhone 실기기 설치는 이 패키지에서 수행하지 않았습니다.
- systemd 타이머는 예시 파일입니다. 서버의 실제 설치 경로를 반영해 설치·활성화해야 합니다.
- 백업은 호스트 backups/에 복사됩니다. 서버 전체 장애에 대비해 다른 서버/오브젝트 스토리지로 별도 복제해야 합니다.
- Toss 실결제와 SMTP 이메일 발송은 실제 운영 키 및 계약/설정이 있어야 검증할 수 있습니다.

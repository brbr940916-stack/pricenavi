가격 추적기 v13 - 출시 준비 도구

v12의 기존 앱/DB 구조를 유지하고 출시 준비 기능을 추가했습니다.

추가 기능
1) GET /terms : 서비스 이용약관 운영 초안
2) GET /privacy : 개인정보 처리방침 운영 초안
3) GET /api/account/export : 로그인한 사용자의 본인 계정 데이터 JSON 내보내기
4) restore_db.py : 검증된 SQLite 백업 복구(현재 DB를 먼저 안전 복사)
5) LAUNCH_CHECKLIST_KO.txt : 정식 출시 전 확인 목록

데이터 내보내기
- 로그인한 상태에서 브라우저 또는 API 클라이언트로 GET /api/account/export 호출
- 본인 데이터만 제공하며 세션 토큰, 비밀번호 해시, 빌링키 등 비밀 정보는 포함하지 않습니다.

백업 복구
- 반드시 앱을 정지한 뒤 실행하세요.
- Windows: RESTORE_WINDOWS.bat 백업파일경로
- Python: python restore_db.py 경로/backup.sqlite3 --yes
- 현재 DB가 있으면 .pre-restore-타임스탬프 파일로 보존합니다.

실행/업데이트
- 기존 v12의 .env와 DATABASE_PATH를 유지하고 앱을 중지한 뒤 v13 파일로 교체하세요.
- 기존 DB 스키마 변경은 없습니다.
- 기존 .env의 APP_SECRET, Toss 키, SMTP 키를 새로 만들거나 공개 저장소에 올리지 마세요.

중요
- 이용약관/개인정보 처리방침은 초안입니다. [대괄호] 정보를 실제 값으로 교체하고 실제 데이터 처리, 위탁사, 보유기간을 반영해 법률 검토 후 게시해야 합니다.
- 원격 백업 업로드, 자동 복구, PG 실결제 테스트는 포함되지 않습니다.
- 기존 v12의 Toss 실계정 연동, SMTP 발송, 서버 배포가 실제 운영 환경에서 검증된 것은 아닙니다.

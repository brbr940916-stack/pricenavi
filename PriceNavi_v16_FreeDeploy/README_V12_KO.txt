가격 추적기 v12 - 보안 및 운영 안정화

이번 버전은 업로드된 v10 원본의 cloud_app.py 및 DB 구조를 기준으로 v11 기능을 포함한 상태에서 보안/운영 기능을 추가한 업데이트입니다. 기존 SQLite DB는 시작 시 추가 컬럼/테이블을 자동 마이그레이션합니다.

추가 기능
- SMTP가 설정된 경우 신규 가입 이메일 인증(24시간, 1회 링크)
- 비밀번호 재설정 이메일(30분, 1회 링크); 변경 시 기존 세션 폐기
- IP별 로그인/가입 실패 15분 10회 제한
- CSP, nosniff, frame 차단, Referrer-Policy, Permissions-Policy
- 자동결제 실패 1/6/24시간 간격 재시도, 총 4회 실패 시 구독 종료 및 무료 전환
- 관리자 API의 미납/최종 실패 구독 통계
- backup_db.py: SQLite online backup + integrity_check + 보존기간 정리

실행
1. v12 ZIP 압축 해제
2. v10/v11에서 사용하던 .env와 DB 파일을 백업한 뒤 같은 위치/경로로 설정
3. SMTP 설정을 입력하고 REQUIRE_EMAIL_VERIFICATION=true로 설정
4. python cloud_app.py 실행 또는 Docker Compose 배포
5. 수동 백업: 로컬은 python backup_db.py (Windows는 BACKUP_WINDOWS.bat), Docker는 docker compose exec app python backup_db.py
6. 운영 서버에서는 cron/systemd timer 등으로 하루 1회 백업 스크립트를 실행하세요.

계정 API
- 이메일 인증: 가입 시 이메일 링크를 열면 /verify?token=...에서 인증
- 비밀번호 재설정 요청: POST /api/password/forgot JSON {"email":"..."}
- 비밀번호 변경: 이메일 링크 /reset?token=...에서 새 비밀번호(12자 이상) 설정
- SMTP_HOST 미설정이면 신규 계정은 자동 인증 상태로 생성되어 로컬 테스트가 가능합니다.

주의 및 미완료
- SMTP 실발송은 실제 SMTP 계정이 있어야 확인할 수 있습니다.
- Toss 실카드 승인/빌링키/환불은 실가맹점 키 및 Toss 계약이 있어야 검증할 수 있습니다.
- 결제 재시도는 자동결제 API 호출의 응답/네트워크 오류를 재시도하는 애플리케이션 정책이며, PG 계약상 재청구 정책은 운영 전 Toss와 확인해야 합니다.
- SQLite 파일 백업은 로컬 볼륨 내 백업입니다. 장애 대비를 위해 별도 원격 저장소로 복사하는 작업을 운영 환경에 추가해야 합니다.
- IP 제한은 프록시 뒤에서 실제 클라이언트 IP를 신뢰하도록 별도 설정하지 않았으므로, reverse proxy 환경에서는 서버가 보는 peer IP 기준입니다.
- 정식 공개 전 취약점 점검, 개인정보 처리방침/약관, 이메일 발송 도메인 인증, 백업 복구 리허설이 필요합니다.

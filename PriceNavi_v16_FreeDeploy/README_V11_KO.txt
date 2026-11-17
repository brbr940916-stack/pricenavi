가격 추적기 v11 — 자동결제/운영 대시보드
=======================================

v11은 업로드된 v10 원본의 cloud_app.py를 직접 수정한 업그레이드입니다. 기존 users/products/price_history/alerts/payment_orders 테이블을 유지하고 새 구독/환불/운영 테이블을 추가합니다. 기존 DB는 init_db() 실행 시 새 테이블만 생성하므로 기존 회원·상품·결제 주문은 유지됩니다.

추가 기능
- Toss 카드 자동결제 등록창(requestBillingAuth)
- authKey로 빌링키 발급
- 빌링키 암호화 저장(Fernet; APP_SECRET에서 파생)
- 카드 등록 직후 첫 결제 및 이후 30일마다 서버 자동 청구
- 사용자 자동갱신 해지 (현재 결제 기간까지 PRO 유지)
- 관리자 운영 대시보드: 전체 회원, 유료회원, 결제액, 활성 자동구독, 결제 실패
- 관리자 결제내역 및 전액 환불 요청
- Toss PAYMENT_STATUS_CHANGED 웹훅 수신 및 API 조회 대사
- /health 데이터베이스 상태 확인

설치/배포
1. .env.example을 복사해 .env로 만들고 APP_SECRET, DOMAIN, ADMIN_EMAIL, Toss 키를 입력합니다.
2. Docker 배포: docker compose up -d --build
3. 최초 관리자 이메일로 가입하면 관리자 권한이 부여됩니다.
4. Toss 콘솔에 웹훅 URL https://도메인/webhooks/toss 를 등록하고 PAYMENT_STATUS_CHANGED 이벤트를 선택합니다.
5. /health 에서 status=ok, database=ok를 확인합니다.

자동결제 설정 필수
- Toss Payments 자동결제(빌링)는 별도 계약/심사가 필요합니다. 일반 단건 결제 키만으로는 자동결제가 되지 않을 수 있습니다.
- TOSS_CLIENT_KEY/TOSS_SECRET_KEY는 자동결제 계약이 적용된 키를 사용해야 합니다.
- BILLING_INTERVAL_DAYS 기본값은 30, PRO_PRICE_KRW 기본값은 3900원입니다.
- 빌링키는 APP_SECRET에서 파생한 Fernet 키로 암호화해 DB에 저장합니다. APP_SECRET을 변경하면 기존 빌링키를 복호화할 수 없으므로 절대 분실/변경하지 말고 백업을 안전하게 보관하세요.
- APP_SECRET은 강한 무작위 32자 이상으로 설정하고 공개 저장소에 올리지 마세요.

중요한 제한/미완성
- 실제 Toss 키로 카드 등록, 청구, 환불을 수행하지 않았습니다. 이 실행 환경에는 가맹점 키/계약이 없습니다.
- 자동결제 실패 시 past_due로 표시하지만 자동 재시도, 카드 재등록 화면, 실패 안내 이메일은 아직 없습니다.
- 환불 화면은 전액 환불만 제공합니다. 부분 환불은 관리자 API/화면에 추가하지 않았습니다.
- 웹훅 이벤트는 DB에 기록하고 PAYMENT_STATUS_CHANGED의 paymentKey를 Toss 조회 API로 확인합니다. 이벤트 서명 검증 대신 결제사 API 조회로 신뢰성을 확보하는 방식입니다.
- 결제 매출 지표는 DB의 결제 승인/환불 상태를 기반으로 한 내부 합계이며 회계/정산자료를 대체하지 않습니다.
- 단일 앱 인스턴스 실행을 전제로 합니다. 다중 인스턴스에서는 청구 중복 방지를 위한 분산 락/작업 큐가 추가로 필요합니다.
- 사업자/전자상거래 법률 의무, 약관, 개인정보 처리방침, 청약철회/환불 정책 검토 후 운영하세요.

기존 파일
- cloud_app.py: 클라우드 서버
- app.py: 기존 로컬 앱 (v11의 자동결제 기능은 cloud_app.py에만 적용)
- Dockerfile/docker-compose.yml/Caddyfile: 클라우드 배포


v12 업데이트: 보안/운영 안정화 내용은 README_V12_KO.txt를 참고하세요.

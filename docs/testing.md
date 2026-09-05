# 검증과 제한사항

## 검증 수준을 구분한다

| 수준 | 근거 | 의미 |
|---|---|---|
| 기존 장비 동작 | 유지보수자의 확인: LTE 접속·IMS 등록·통화·SMS 모두 성공 | 반입된 구성의 실사용 기준점. 단말 모델·펌웨어·패킷 캡처는 현재 Git 이력에 없음 |
| 기존 자동 검사 | 수정 전 `9cacfbd`의 SMSC 테스트 31개 통과 | 기존 테스트에 포함된 SIP/TPDU/handler 동작 |
| 이번 코드 검사 | 아래 회귀 테스트 및 Linux 초기화 검사 | 수정한 실패 경로, 재전송 처리, 설정 원본 보존 |
| 이번 장비 통합 검사 | 수행하지 않음 | 수정 후 실제 LTE/IMS/통화/SMS 성공을 새로 주장하지 않음 |

이번 검증은 실제 시험 DB·호스트 커널 설정·SDR를 변경하지 않았다. Docker 초기화 검사는 네트워크와 장비 접근이 없는 일회용 Ubuntu 컨테이너에서 수행했다.

## 실행 명령

일반 개발 검사에는 Docker가 필요 없다.

```bash
uv sync --locked
uv run poe test
```

전체 Python 검사 중 Docker 검사는 기본적으로 건너뛴다. SMSC만 실행하려면 `uv run poe smsc-test`를 사용한다.

eNB 초기화 검사까지 포함하려면 실행 중인 Docker와 `ubuntu:22.04` 이미지가 필요하다.

```bash
docker pull ubuntu:22.04
RUN_CONTAINER_TESTS=1 uv run poe test
```

Docker 검사는 실제 `srsenb` 대신 입력 설정을 검사하는 실행 파일을 사용한다. `--network none`, 권한 추가 없음, USB 장치 마운트 없음으로 실행한다. **srsRAN 자체의 설정 파싱·컴파일·무선 동작을 검사하는 테스트가 아니다.**

Compose 구조 검사는 운영 `.env` 없이도 할 수 있다. `--no-env-resolution`을 제공하는 Compose 버전에서 다음을 실행한다.

```bash
docker compose --env-file .env.example config --no-env-resolution --quiet
```

이 명령은 `.env.example`로 변수 치환과 Compose 모델을 검사한다. 서비스의 `env_file`을 실제로 읽거나 init 스크립트·이미지·Diameter 연결을 검사하지 않는다. 정상 운영 환경에서는 `.env`를 준비한 뒤 `docker compose config --quiet`도 확인한다.

## 이번 변경의 회귀 검사

### SMSC

[`test_smsc.py`](../infrastructure/smsc/tests/test_smsc.py)에 기존 테스트와 함께 유지한다.

- `100 Trying` 등 1xx 뒤에 최종 응답이 오면 그때 MO 결과를 반환한다.
- 1xx가 와도 원래 MT 대기 제한시간을 연장하지 않는다.
- 동일 MO 요청이 pending 상태에서 재전송되어도 MT를 다시 만들지 않는다.
- 최종 결과가 나온 뒤 같은 MO를 받으면 캐시된 응답을 재전송한다. `408` timeout 결과도 포함한다.
- 캐시 만료 후에는 새 처리가 가능하다. 재전송 자체가 보존 시간을 연장하지 않는다.
- 다른 Via branch, Call-ID/CSeq 또는 송신 endpoint는 별도 요청으로 처리한다.
- Via의 `received`/`rport` 변경은 새 트랜잭션으로 보지 않는다. 원래 응답의 전체 Via stack은 유지한다.

핵심 새 회귀 사례는 수정 전 9개 실패를 확인한 뒤 구현했다. 기존 타임아웃 테스트의 실제 sleep은 가짜 monotonic clock으로 바꿔 경계 시각을 직접 검증한다. [RFC 3261 §17.1.2.2](https://www.rfc-editor.org/rfc/rfc3261.html#section-17.1.2.2)의 임시/최종 응답 구분을 참고했다. 이 검사가 완전한 SIP 적합성 인증을 뜻하지는 않는다.

### 가입자 등록

[`test_provision_subscribers.py`](../tests/test_provision_subscribers.py)는 외부 Docker/HTTP 경계만 대체하여 실제 provisioning 함수를 호출한다.

- Mongo 쓰기 실패 시 완료로 넘어가지 않는다.
- PyHSS 조회 실패, 중복 레코드, malformed JSON, ID 누락, PUT/PATCH 오류를 중단 사유로 처리한다.
- 실제 반환된 APN/AUC ID를 후속 subscriber 데이터에 사용한다.
- AMF 입력, 신규 가입자와 기존 가입자의 갱신 경로를 검사한다.
- PyHSS 갱신 payload에 SQN·기존 정책 초기화 필드를 넣지 않는다. MongoDB의 SQN·세션 보존은 코드 검토로 확인했으며 실제 DB 상태 비교 검사는 수행하지 않았다.
- 전체 가입자 입력 오류를 쓰기 전에 거부한다.
- iFC 원본 마운트 검사와 활성화 안내를 확인한다.

초기 회귀 사례 13개에서 수정 전 실패를 확인했다. 이후 생성/main 흐름과 API 예외 사례 검사를 보강하고, MongoDB 상태 대신 생성된 코드 문자열만 검사하던 사례는 제거했다. 실제 API 계약은 고정 버전 [PyHSS 1.0.2 apiService.py](https://github.com/nickvsnetworking/pyhss/blob/1.0.2/services/apiService.py) 및 [database.py](https://github.com/nickvsnetworking/pyhss/blob/1.0.2/lib/database.py)를 확인했다. 운영 DB의 내용·스키마 상태나 API 준비 상태까지 로컬 mock 검사로 보장하지 않는다.

### eNB 초기화

[`test_srsenb_init.py`](../tests/test_srsenb_init.py)는 실제 Bash init 스크립트를 Ubuntu 22.04에서 실행한다.

- `/etc/srsran` 마운트와 `/root/.config/srsran` fallback 두 경로를 검사한다.
- MCC, TX gain, TAC, EARFCN 치환 결과와 인자 전달을 확인한다.
- `sib.conf`, `rr.conf`, `rb.conf`가 runtime 작업 디렉터리에 존재해야 한다.
- 설정 원본은 읽기 전용 마운트에서 사용할 수 있어야 하며 전후 bytes가 같아야 한다.

수정 전에는 runtime 작업 디렉터리 조건에서 실패했다. 수정 후 두 입력 경로 모두 통과했다. 초기 Docker 시도에서는 환경 기동 지연으로 timeout이 있었고, 단독 컨테이너 실행을 확인한 뒤 재실행해 실제 코드 실패와 수정 후 성공을 구분했다.

## 검증 기록 — 2026-09-05

호스트: macOS arm64, Python 3.13.15, pytest 9.0.3. Docker Engine 29.7.2, Compose 5.5.0. eNB 검사 이미지: `ubuntu:22.04` (pull 시 digest `sha256:2edbbc5dc405e9612ba3584ce95480277e3eb374407b5505fe26f17df77c7dbc`).

| 검사 | 결과 |
|---|---|
| 기존 SMSC baseline | 31 passed |
| 수정 후 Python 검사, Docker 제외 | 63 passed, 2 skipped |
| `RUN_CONTAINER_TESTS=1 uv run poe test` | 65 passed |
| Compose 변수 치환·모델 검사 | 통과 |
| 저장소 Bash 스크립트 `bash -n` | 23개 통과 |
| `git diff --check` | 통과 |
| Markdown 로컬 링크·코드 블록 | 문서 6개, 로컬 링크 35개 확인 |

전체 EPC/IMS 이미지 빌드, 실제 DB provisioning, 호스트 setup 실행, 실제 SDR·단말 재검증은 수행하지 않았다. 이 범위는 이번 변경을 장비에 적용할 때 아래 표로 확인한다.

## 장비 적용 후 확인표

| 순서 | 확인할 동작 | 기록할 증거 |
|---|---|---|
| 1 | EPC/IMS와 eNB가 한 PC에서 기동 | Git SHA, 이미지 ID, 포트/주소, 초기화 로그 |
| 2 | 두 UE LTE 접속 및 APN 할당 | eNB/MME 로그, 단말별 APN/IP |
| 3 | 두 UE IMS 등록 | P/I/S-CSCF·PyHSS 로그, REGISTER 최종 결과 |
| 4 | UE1→UE2 및 UE2→UE1 통화 | 연결/해제, 양방향 음성, RTP 관찰 |
| 5 | 양방향 짧은 GSM 7-bit SMS | 송·수신 화면, Call-ID와 SIP 결과 |
| 6 | 동일 MO 재전송 | MT 생성 횟수와 수신 중복 여부 |
| 7 | 수신 UE 미등록 / MT 응답 지연 | 기대 실패/timeout과 실제 반환 코드 |
| 8 | 설정을 유지한 provision 재실행 | DB ID·SQN 보존, 재접속/IMS 인증 |
| 9 | eNB 재시작 | 원본 Git diff 불변, runtime 치환값 |
| 10 | eNB 중지→EPC 중지→재기동 | bridge·라우트 복구 후 전체 서비스 재확인 |

실험 결과는 성공/실패와 함께 장비·단말 모델, 펌웨어, 날짜, 변경한 설정을 기록한다. “통화됨”만으로 SMS 재전송이나 다른 PLMN도 확인되었다고 확대하지 않는다.

## 남아 있는 범위와 제한

### SMSC

현재는 UDP SIP MESSAGE와 DCS `0x00` GSM 7-bit 변환을 위한 실험 구현이다. 한글/이모지용 UCS-2, UDH 기반 긴 메시지 분할·재조립, 완전한 RP-ACK/RP-ERROR·전달 보고, 영속 store-and-forward는 지원 범위가 아니다. 지원하지 않는 TPDU가 항상 명확히 거부된다고 보장하지도 않으므로 짧은 기본 문자 메시지부터 확인한다.

정상 디코딩된 MO만 트랜잭션 캐시에 들어간다. 기본 MT 대기는 32초, 완료 응답은 완료 시점부터 32초 보존한다. key는 송신 IP/포트, 최상위 Via의 sent-protocol/sent-by 및 branch, Call-ID/CSeq다. branch가 없으면 원래 top Via 문자열로 비교하는 제한적 fallback을 사용한다.

캐시는 프로세스 메모리에 있고 재시작하면 소멸한다. 시간 만료는 있지만 엔트리 수 상한·속도 제한은 없다. MT UDP 패킷의 자동 재전송도 없다. MT 응답 매칭은 기존처럼 Call-ID에 의존하며 전체 SIP 응답 출처/branch 검증을 제공하지 않는다. 높은 부하·유실률·임의 패킷을 시험할 때는 이 한계를 실험 대상의 오류와 구분한다.

SIP parser는 제한된 헤더 형식을 다루며 folded/compact header, Content-Length 엄격 검증 등 전체 SIP parsing 규칙을 구현하지 않는다. 테스트의 자체 encode/decode round-trip 통과만으로 실제 모든 UE TPDU와의 상호운용성을 보장하지 않는다.

### 운영과 재현성

- `.env`만 바꾸어 임의 PLMN/서브넷으로 이전할 수는 없다. 고정 도메인·호스트 라우트·IPv6 템플릿을 함께 검토한다.
- `setup_host.sh`의 route readiness 판정, cpufreq 미지원 환경, 서비스 초기화의 sleep/무한 대기는 추가 개선 후보다.
- build/start task의 마지막 성공 문구가 중간 명령 실패를 가릴 수 있다. 로그와 실제 상태를 확인한다.
- 모든 서비스의 healthcheck와 readiness 종속성이 마련되어 있지 않다. Compose 시작 순서가 애플리케이션 준비를 보장하지 않는다.
- 한 스택을 위한 전역 이름·호스트 포트가 있다. 여러 스택 병렬 실행은 별도 설계가 필요하다.
- DB 관리 포트, 기본 자격 증명, privileged 장치 접근 등은 실험 환경 가정에 속한다. 이번 작업은 배포 보안 감사가 아니다.
- Docker 빌드 입력이 전부 고정되어 있지 않다. 마지막 동작 이미지 ID를 보관하고 업그레이드를 별도 실험으로 취급한다.

위 항목은 이번에 수정했다고 주장하는 기능이 아니라, 현재 구성의 적용 범위를 명확히 하기 위한 기록이다.

# 운영 절차

이 문서의 명령은 저장소 루트에서 실행한다. 기본 주소를 사용하는 단일 Ubuntu 실험 호스트 기준이며, 실제 무선 장비를 실행하는 단계와 로컬 코드 검사를 구분한다. 네트워크·주소·포트의 근거는 [구성 문서](architecture-and-history.md)를 참고한다.

## 처음 준비할 때

실제 SDR 운용 호스트에는 Docker Engine, Compose v2 이상, `uv`, Linux SCTP/TUN, systemd와 USB 접근이 필요하다. `setup_host.sh`는 Ubuntu 22.04/24.04용이다. macOS에서는 Python 테스트와 파일 편집이 가능하지만, Docker Desktop 설치만으로 Linux 호스트 설정·SDR 연결까지 동등하게 준비되는 것은 아니다.

```bash
git clone https://github.com/jisung-02/volte_testbed.git
cd volte_testbed
cp .env.example .env   # 최초 1회만; 기존 .env를 덮어쓰지 않는다
uv sync --locked
docker version
docker compose version
uv run poe test
```

Docker가 없다면 [Docker의 Ubuntu 설치 안내](https://docs.docker.com/engine/install/ubuntu/), uv는 [공식 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)를 따른다. 설치 방식에 따라 Compose 패키지 이름이 다르므로 `docker compose version`으로 실제 사용 가능 여부를 확인한다.

`.env`에는 `KEY=value` 형태를 사용한다. 가입자 스크립트는 단순 대입 형식을 읽으며 shell 변수 확장, 따옴표 제거, 줄 끝 주석 처리를 하지 않는다. 예를 들어 `MNC=01`은 맞지만 `MNC="01"`이나 `MNC=01 # lab`은 같은 값으로 해석되지 않는다. `enb-run`은 `.env`를 shell로 읽으므로 신뢰하는 설정 파일만 사용한다.

## 설정 항목과 변경 범위

| 항목 | 의미와 주의할 연결 |
|---|---|
| `MCC=001`, `MNC=01`, `TAC=1` | SIM/단말과 일치시킨다. 다른 PLMN은 고정 IMS 도메인도 함께 검토 |
| `TEST_NETWORK`, 각 `*_IP` | 모든 서비스와 eNB가 같은 bridge 주소 계획을 사용 |
| `DOCKER_HOST_IP` | DNS 템플릿에서 사용하는 호스트 주소; 모든 통신의 bind 주소를 바꾸는 설정은 아님 |
| `SGWU_ADVERTISE_IP`, `UPF_ADVERTISE_IP` | GTP-U 상대가 접근할 주소. 단일 PC 기본값은 각각의 bridge IP |
| `UE_IPV4_INTERNET`, `UE_IPV4_IMS`, `UPF_IP` | 호스트 라우트와 P-CSCF/rtpengine의 UE 복귀 경로도 함께 확인 |
| `UPF_*_APN_IF_NAME`, `UPF_TUNTAP_MODE` | APN별 TUN 인터페이스. 기본 `ogstun`, `ogstun2`, `tun` |
| `SMF_DNS1`, `SMF_DNS2` | SMF가 사용하는 DNS 설정 |
| `SRSENB_TX_GAIN`, `SRSENB_DL_EARFCN` | eNB 시작 시 runtime 설정에 주입. 채널 변경은 `sib.conf`와 단말 지원 band도 확인 |
| `PYHSS_URL` | 호스트에서 접근하는 PyHSS 관리 API, 기본 `http://localhost:8080` |
| `UE{N}_IMSI/KI/OPC/AMF/MSISDN` | N=1..9. 빈 번호를 건너뛰며 정의된 가입자 처리. AMF 생략 시 `8000` |
| `SMSC_MSISDN`, `SMSC_LOG_LEVEL` | 필요하면 `.env`에 추가. 기본 `9999`, `INFO` |

IMSI는 5~15자리 숫자, MSISDN은 1~15자리 숫자, KI/OPC는 각각 32자리 hex, AMF는 4자리 hex여야 한다. 이 형식 검사는 SIM에 실제로 기록된 키나 PLMN 일치 여부까지 검증하지 않는다. 가입자 식별자와 키는 실제 시험 SIM에 맞게 입력한다.

## 호스트 준비와 최초 기동

`setup-host` task 안에서 `sudo`를 호출하므로 `sudo uv ...`를 중첩할 필요가 없다. 이 단계는 커널 모듈, sysctl, udev, 리소스 제한, CPU governor와 systemd 서비스를 호스트에 설치한다.

```bash
uv run poe setup-host
# uv와 분리해서 실행할 수도 있다:
# sudo ./setup_host.sh --all
```

호스트 라우트는 Docker bridge 생성 전에는 적용되지 않을 수 있다. 그래서 아래처럼 **EPC 기동 후 라우트를 확인·등록**한다. 설정 스크립트가 라우트 단계에서 실패했다면 bridge를 준비한 뒤 원인을 확인하고 `setup-host`를 재실행한다. CPU governor를 제공하지 않는 환경도 `--all`이 실패할 수 있으므로 출력과 `--check` 결과를 확인한다.

```bash
uv run poe epc-build
uv run poe enb-build
uv run poe epc-run
uv run poe epc-status
```

현재 build task는 여러 shell 명령을 순서대로 실행한다. 마지막 `Done`만으로 이미지 빌드 성공을 판단하지 말고 Docker build 오류와 필요한 이미지 생성 여부를 확인한다. `epc-status`의 `Up`도 애플리케이션 준비 완료를 보장하지 않는다. DB, PyHSS, Diameter peer 초기화에는 시간이 필요하다.

```bash
docker compose logs --tail=100 mongo mysql pyhss hss mme icscf scscf pcscf
curl --fail --silent --show-error 'http://localhost:8080/apn/list?page=0&page_size=1'
```

PyHSS가 정상 JSON 배열을 반환하고 초기화 오류가 없는 것을 확인한 뒤:

```bash
sudo systemctl restart volte-testbed-routes
uv run poe provision
uv run poe enb-run
uv run poe enb-logs
```

`enb-run`은 연결된 SDR에서 실제 eNodeB를 시작한다. `srsenb started` 출력만으로 무선 기동 성공을 판정하지 말고 로그의 장치 초기화와 MME 연결 결과를 확인한다. 그 뒤 UE를 접속시킨다.

## 라우트 확인과 복구

Ubuntu 호스트에서 bridge가 존재하는지, UPF까지 직접 연결되는지를 먼저 본다.

```bash
ip link show br-volte
ip -4 route show 172.22.0.0/24
ip -4 route get 172.22.0.8
```

기본 구성에서는 UPF 경로가 `dev br-volte`로 나와야 한다. 일반 인터넷 gateway를 경유하는 출력은 Docker bridge가 준비되었다는 뜻이 아니다.

```bash
sudo systemctl restart volte-testbed-routes
ip -4 route show 10.10.10.0/24
ip -4 route show 10.20.20.0/24
systemctl status volte-testbed-routes --no-pager
```

두 UE 대역에 `via 172.22.0.8`이 보여야 한다. 서비스 실패 원인은 `journalctl -u volte-testbed-routes -n 50 --no-pager`로 확인한다. 기본값을 변경했다면 `setup_host.sh`가 생성하는 service와 `scripts/add_ue_routes.py`의 주소도 변경해야 한다. 이미 설치된 service 파일은 저장소 파일만 고쳐서는 바뀌지 않는다.

## 가입자 등록의 동작

`uv run poe provision`은 다음 순서로 동작한다.

1. `.env`의 UE1~UE9를 읽고 전체 입력 형식 및 중복 IMSI를 검사한다.
2. MongoDB의 Open5GS 가입자를 생성하거나 갱신한다. 기존 SQN과 세션 설정을 보존하고, IMS 세션이 없으면 추가한다.
3. PyHSS의 APN, AUC, subscriber, IMS subscriber를 조회 후 생성/갱신한다. DB가 반환한 실제 APN/AUC ID를 연결한다.
4. PyHSS에 마운트된 iFC **원본**과 저장소 파일이 같은지 확인한다. 컨테이너 재시작이나 runtime iFC 활성화는 수행하지 않는다.

실패하면 오류와 비정상 종료 코드를 반환한다. 앞서 성공한 쓰기는 남을 수 있다. MongoDB와 MySQL을 묶는 트랜잭션/자동 rollback은 없으므로 원인을 고친 뒤 같은 설정으로 다시 실행한다. 실패를 해결하려고 DB 볼륨을 지우거나 SQN을 0으로 초기화하지 않는다.

기존 APN 정책, subscriber의 enabled/AMBR, IMS 가입자의 커스텀 iFC·serving 상태는 보존한다. 반면 키·AMF·MSISDN과 subscriber의 기본 APN 및 APN 목록은 이 테스트베드의 `internet`/`ims` 설정으로 갱신한다. 기존 IMS 가입자의 iFC가 비어 있거나 잘못되어 있다면 운영자가 API에서 바로잡아야 한다.

조회는 PyHSS 1.0.2의 `/list?page=0&page_size=0`을 사용한다. 작은 실험 DB를 위한 전체 조회이며, 대규모 DB나 동시 provisioning은 지원 범위가 아니다. API provisioning lock을 켠 구성은 현재 스크립트에 인증 키 연동이 없어 실패한다. HTTP `0`은 HTTP 응답을 얻지 못했다는 뜻이며 URL·프로세스·연결을 확인한다.

## 변경 후 적용

| 바꾼 것 | 실행할 것 | 추가 확인 |
|---|---|---|
| 가입자 키/AMF/MSISDN | `uv run poe provision` | 종료 코드, DB 값, UE 재접속 |
| SMSC Python 코드 | `docker compose build smsc` 후 `docker compose up -d --no-deps --force-recreate smsc` | 로그, SMS 양방향; 재시작하면 pending/cache 상태 소멸 |
| eNB 설정 / RF 값 | `uv run poe enb-run` | 새 runtime 경로와 RF/MME 초기화 로그 |
| `.env`의 서비스 환경 변수 | `docker compose up -d --force-recreate 서비스명` | 관련 서비스도 값이 맞는지 확인 |
| DNS 템플릿 | `docker compose restart dns` | 이름 해석 및 기존 프로세스의 캐시 |
| Kamailio 설정 | 해당 CSCF 재시작 | SIP/IMS 등록; 기존 통화에 영향 가능 |
| 호스트 네트워크 | 수정한 `setup_host.sh --network` 적용 | 생성된 service 주소, bridge와 라우트 |

### iFC 변경

```bash
docker restart pyhss
docker logs --tail=100 pyhss
# PyHSS 초기화/API가 다시 준비된 뒤:
docker restart scscf
```

이후 UE를 재등록한다(예: 비행기모드 전환). PyHSS는 시작 시 iFC 원본을 runtime 위치에 복사하고, S-CSCF는 가입자 프로필을 유지하므로 둘을 구분해야 한다. `provision`의 원본 마운트 검사 통과가 runtime 적용 완료를 의미하지 않는다. PyHSS/S-CSCF 재시작은 진행 중인 실험 밖에서 수행한다.

## 중지와 재실행

짧은 중지로 Compose 네트워크를 유지하려면:

```bash
uv run poe enb-stop
docker compose stop
# 재개:
docker compose start
uv run poe epc-status
sudo systemctl restart volte-testbed-routes
uv run poe enb-run
```

컨테이너와 Compose 네트워크를 내리려면 eNB부터 제거한다.

```bash
uv run poe enb-stop
uv run poe epc-stop
```

eNB가 네트워크에 남아 있으면 Compose가 네트워크를 제거하지 못할 수 있다. `epc-stop`은 기본적으로 DB named volume을 보존한다. 재개는 `epc-run` → 준비 확인 → 라우트 복구 → `enb-run` 순서다. 가입자 설정을 바꾸지 않았다면 매번 `provision`을 실행할 필요는 없다. `down -v`나 volume 삭제는 데이터 초기화 작업이며 일반적인 재실행 절차에 포함하지 않는다.

## 증상별 확인 순서

| 증상 | 먼저 볼 곳 | 다음 판단 |
|---|---|---|
| 컨테이너/네트워크 이름 충돌 | `docker ps -a`, `docker network inspect docker_open5gs_default` | 다른 스택인지 식별한 뒤 그 스택의 정상 종료 절차 사용 |
| LTE 접속 실패 | `docker logs srsenb`, `docker compose logs mme hss` | SDR 초기화, S1AP, PLMN/TAC, SIM 키 순으로 확인 |
| LTE는 되지만 IMS 등록 실패 | `docker compose logs pcscf icscf scscf pyhss` | IMS APN, DNS, 인증, IPsec, 가입자 iFC 확인 |
| 통화 연결 후 무음/단방향 음성 | `docker logs rtpengine`, `docker exec rtpengine ip route`, `docker exec pcscf ip route` | UE 복귀 경로, RTP 주소/포트, UPF 상태 확인 |
| SMS `415` | SMSC 로그와 Content-Type | `application/vnd.3gpp.sms`인지 확인 |
| SMS `400` | SMSC TPDU decode 로그 | 잘못된 TPDU 또는 지원하지 않는 문자 인코딩 확인 |
| SMS `480` | I/S-CSCF와 수신 단말 등록 상태 | 미등록·busy 등 여러 원인이 있음; 코드 하나로 미등록을 단정하지 않음 |
| SMS `408` | SMSC·I-CSCF 로그 | MT 최종 응답이 제한시간 안에 돌아왔는지 확인 |
| 등록 완료라고 나오지만 서비스 실패 | provisioning 종료 코드와 해당 DB/API | 관리 데이터 등록 성공과 LTE/IMS 런타임 성공은 별개 |

기본 로그 수집:

```bash
docker compose logs --since=10m --timestamps mme hss pyhss pcscf icscf scscf smsc rtpengine
docker logs --since=10m --timestamps srsenb
```

기존 README의 `kamctl ul show`는 이 이미지의 IMS 전용 usrloc 모듈/관리 소켓 설정에 따라 바로 동작하지 않을 수 있다. 명령 실패를 “가입자 없음”으로 해석하지 말고 CSCF 로그와 실제 REGISTER 흐름으로 교차 확인한다. 두 CSCF 설정은 `db_mode=0`이므로 SQL 테이블만으로 메모리상의 등록 상태를 단정하지 않는다.

## 실험 기록과 복구 기준점

실험마다 Git SHA, 이미지 ID, 변경 diff, 적용한 설정, 단말/SIM 식별용 별칭, 양방향 결과, 로그 시간대를 함께 남긴다. KI/OPC가 들어 있는 `.env` 원본과 DB 백업은 공개 Git에 넣지 않는다.

```bash
git rev-parse HEAD
git diff --stat
docker compose images
docker inspect --format '{{.Name}} {{.Image}}' srsenb
```

코드 변경과 환경 변경을 한 실험에서 함께 수행하면 원인 분리가 어려워진다. 마지막으로 성공한 Git SHA와 이미지 ID를 기록하고, 실험용 설정 변경을 한 묶음씩 적용한다. 코드 rollback은 기존 checkout의 미커밋 작업을 먼저 보존한 다음 수행한다. DB의 SQN은 통신 중 진전될 수 있으므로 오래된 DB snapshot 복원을 일반적인 코드 rollback과 동일하게 취급하지 않는다.

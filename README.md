# volte_testbed

VoLTE 테스트베드 인프라 — EPC, RAN, IMS 를 단일 도커 스택으로 통합한 4G/VoLTE 종단간 테스트 환경.

영문판: [README.en.md](./README.en.md)

## 사전 요구사항

### 하드웨어
- SDR: USRP B210 (현 설정값). BladeRF / LimeSDR 도 사용 가능 — `infrastructure/srsenb/enb.conf` 의 `device_name` / `device_args` 조정 필요
- USB: SDR 연결용. `setup-host` 가 udev 규칙 자동 설치

### 소프트웨어 (호스트에 Docker + uv 설치)

#### macOS
```bash
brew install --cask docker   # Docker Desktop
brew install uv
```

#### Ubuntu
```bash
# Docker
sudo apt update
sudo apt install -y docker.io docker-compose-plugin

# uv
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 빠른 시작

```bash
# 최초 1회: 환경 변수 파일 생성
cp .env.example .env

# poethepoet 설치 (package = false 라 빌드 단계 없음)
uv sync

# 호스트 시스템 셋업 (서버에서 1회, root 권한 필요)
sudo uv run poe setup-host

# 베이스 이미지 빌드
uv run poe epc-build

# srsenb 이미지 빌드 (srsRAN_4G 컴파일 — 5–15 분 소요)
uv run poe enb-build

# 4G EPC + IMS 컨테이너 기동
uv run poe epc-run

# 컨테이너 상태 확인 (모두 Up 인지)
uv run poe epc-status

# srsenb 기동 (USRP B210)
uv run poe enb-run

# 가입자 등록
uv run poe provision
```

> `setup-host` 한 번이 호스트 sysctl 과 UE subnet route 영속화 (systemd) 까지 같이 처리한다. 별도 네트워크 셋업 task 는 필요 없음.

## 사용 가능한 task

`uv run poe` 로 전체 11개 task 목록 확인.

| 카테고리 | task | sudo |
|---|---|---|
| eNB | `enb-build`, `enb-run`, `enb-stop`, `enb-logs` | — |
| EPC | `epc-build`, `epc-run`, `epc-stop`, `epc-status`, `epc-logs` | — |
| 가입자 | `provision` | — |
| 호스트 셋업 | `setup-host` | ✅ |

## 환경 변수 (`.env`)

`cp .env.example .env` 후 본인 환경에 맞게 조정.

### PyHSS REST API

| 변수 | 의미 |
|---|---|
| `PYHSS_URL` | PyHSS REST API endpoint (`provision` task 가 가입자 등록 시 사용) |

### 인프라 (Docker EPC/IMS)

| 변수 | 의미 |
|---|---|
| `MCC`, `MNC`, `TAC` | PLMN ID (`001` / `01` / `1`) |
| `TEST_NETWORK` | docker bridge 서브넷 (`172.22.0.0/24`) |
| `DOCKER_HOST_IP` | docker 호스트 IP |
| `*_IP` 시리즈 | 각 컨테이너 IP (HSS, MME, SGW, SMF, UPF, PCRF, DNS, RTPENGINE, PYHSS, ICSCF, SCSCF, PCSCF, WEBUI, MONGO, MYSQL, SRS_ENB, ENTITLEMENT_SERVER) |
| `UE_IPV4_INTERNET` / `UE_IPV4_IMS` | UE APN 서브넷 |
| `MAX_NUM_UE` | 최대 UE 수 |

### 테스트 가입자

`UE{N}_IMSI`, `UE{N}_KI`, `UE{N}_OPC`, `UE{N}_AMF`, `UE{N}_MSISDN` (N=1..9). `provision` task 가 비어있는 IMSI 를 만나면 멈춤 — 1..9 중 정의된 만큼 자동 인식.

## 문제 해결

**`sudo: uv: command not found`** — `uv` 가 사용자 PATH (`~/.local/bin`) 에 설치돼 sudo 가 못 찾는 경우.
```bash
sudo -E env "PATH=$PATH" uv run poe setup-host
# 또는 절대 경로
sudo $(which uv) run poe setup-host
```

**`docker: permission denied`** — 사용자가 docker 그룹에 속하지 않은 경우. `setup-host` 가 안내 출력 → `sudo usermod -aG docker $USER` → 로그아웃/로그인.

**컨테이너 이름 / 네트워크 충돌** — 동일 호스트에서 다른 docker compose 스택이 같은 컨테이너 이름 (`hss`, `mme`, ..., `pcscf`) 또는 `docker_open5gs_default` 네트워크를 쓰고 있으면 기동 실패. 한 번에 한 스택만 운영.

**UE 서브넷 (`10.10.10.0/24` / `10.20.20.0/24`) 가 안 닿음** — `epc-stop` (= `docker compose down`) 이 docker bridge 를 destroy 하면서 UPF (`172.22.0.8`) next-hop 도 같이 사라져 route 가 purge 됨. `epc-run` 으로 다시 띄운 뒤 route 재등록:
```bash
sudo systemctl restart volte-testbed-routes
```

## 참조 및 출처

이 testbed 는 [herlesupreeth/docker_open5gs](https://github.com/herlesupreeth/docker_open5gs) 의 구조를 기반으로, 다음 오픈소스 컴포넌트의 도커 구성을 통합한다:

| 컴포넌트 | 프로젝트 | 버전/태그 |
|---|---|---|
| 4G EPC | [Open5GS](https://github.com/open5gs/open5gs) | commit `47d0062c` |
| IMS (P/I/S-CSCF) | [Kamailio](https://github.com/kamailio/kamailio) | commit `6ce33529` |
| eNodeB (SDR) | [srsRAN_4G](https://github.com/srsran/srsRAN_4G) | `release_23_11` |
| IMS HSS | [PyHSS](https://github.com/nickvsnetworking/pyhss) | tag `1.0.2` |
| SDR 추상화 | [SoapySDR](https://github.com/pothosware/SoapySDR) | `soapy-sdr-0.8.1` |
| 미디어 릴레이 | rtpengine | `infrastructure/rtpengine/Dockerfile` 참조 |

기본 OS 이미지: Ubuntu 22.04 (jammy)

## 프로젝트 구조

```
volte_testbed/
├── docker-compose.yml          # EPC + IMS 서비스 정의
├── .env.example                # 환경 변수 템플릿
├── setup_host.sh               # 호스트 OS 셋업 자동화
├── pyproject.toml              # poe task 정의
├── infrastructure/             # 컨테이너 설정 (mme, hss, srsenb, pcscf 등)
└── scripts/
    ├── provision_subscribers.py
    └── add_ue_routes.py
```

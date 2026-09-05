# volte_testbed

EPC·IMS·SDR eNB를 **한 PC에서 함께 실행**하는 4G/VoLTE 실험 환경입니다. [docker_open5gs](https://github.com/herlesupreeth/docker_open5gs)를 기반으로 단일 PC 운용에 맞춘 설정, 가입자 등록, 호스트 준비 및 실행 task를 묶었습니다. 이후 Python SMSC와 다중 Via 응답 보존을 추가했습니다.

유지보수자가 기존 구성에서 실제 단말의 **LTE 접속·IMS 등록·통화·SMS를 모두 확인**했습니다. 새 코드 변경의 자동 검증과 실제 장비 재검증은 [검증 문서](docs/testing.md)에서 구분합니다.

[English](README.en.md)

## 문서

| 문서 | 내용 |
|---|---|
| [구성과 변경 이력](docs/architecture-and-history.md) | 네트워크 도식, IP·포트, 단일 PC 구성, 원본과의 관계, 초기 반입 이후 커밋 분석 |
| [운영 절차](docs/operations.md) | 설정, 최초 기동, 재실행, 가입자 등록, iFC 적용, 라우트 복구, 문제 해결 |
| [검증과 제한사항](docs/testing.md) | 로컬 회귀 테스트, Linux 컨테이너 검사, 장비 검증표, SMSC 범위, 남은 제약 |

## 실행 환경

- 실제 장비 실행: Ubuntu 22.04/24.04 호스트, Docker Engine, Compose v2 이상, `uv`, SCTP/TUN·systemd·USB 접근.
- 기본 SDR: USRP B210. 다른 SDR용 의존성·설정 예시는 있으나 실제 동작 확인 범위는 별도입니다.
- 로컬 개발: Python 3.12 이상. macOS에서도 Python 회귀 테스트를 실행할 수 있습니다.
- 이 스택의 복사본이나 upstream 스택과의 **동시 실행**은 지원하지 않습니다. 고정 컨테이너·네트워크·볼륨 이름과 호스트 포트가 있습니다.

설치 및 설정 형식은 [운영 절차](docs/operations.md#처음-준비할-때)를 따릅니다.

## 빠른 시작

아래는 기본 주소를 사용하는 Ubuntu 실험 호스트 기준입니다. 이미 `.env`가 있다면 복사 단계를 건너뜁니다.

```bash
cp .env.example .env
# .env의 호스트 주소와 시험 SIM의 IMSI/KI/OPC/AMF/MSISDN을 수정
uv sync --locked
uv run poe test

# task 내부에서 sudo 호출
uv run poe setup-host
uv run poe epc-build
uv run poe enb-build
uv run poe epc-run
uv run poe epc-status
```

`Up`은 컨테이너 상태입니다. 로그에서 DB/API 및 Diameter 초기화가 끝났는지 확인합니다. 호스트 준비 중 Docker bridge가 없어 라우트 적용이 실패했다면 [라우트 복구](docs/operations.md#라우트-확인과-복구)를 먼저 확인합니다.

```bash
docker compose logs --tail=100 mysql pyhss hss mme pcscf icscf scscf
curl --fail --silent --show-error 'http://localhost:8080/apn/list?page=0&page_size=1'
sudo systemctl restart volte-testbed-routes
uv run poe provision

# SDR에서 실제 eNB 시작
uv run poe enb-run
uv run poe enb-logs
```

이후 UE를 연결하고 LTE 접속 → IMS 등록 → 양방향 통화 → 양방향 SMS를 확인합니다. 가입자 DB 등록 성공만으로 이 단계들이 검증되는 것은 아닙니다.

## 자주 쓰는 명령

| 작업 | 명령 |
|---|---|
| 전체 목록 | `uv run poe` |
| EPC/IMS 이미지 빌드 | `uv run poe epc-build` |
| EPC/IMS 기동·상태·로그 | `uv run poe epc-run`, `epc-status`, `epc-logs` |
| eNB 이미지 빌드·기동·로그 | `uv run poe enb-build`, `enb-run`, `enb-logs` |
| 가입자 생성/갱신 | `uv run poe provision` |
| 기본 회귀 테스트 | `uv run poe test` |
| SMSC 테스트만 | `uv run poe smsc-test` |
| eNB 중지 후 EPC/IMS 중지 | `uv run poe enb-stop` 후 `uv run poe epc-stop` |
| 호스트 상태 확인 | `sudo ./setup_host.sh --check` |

`epc-stop`은 eNB를 관리하지 않으므로 **eNB부터 중지**합니다. DB 볼륨은 보존됩니다. 다시 기동한 후에는 호스트 UE 라우트를 확인합니다.

## 설정과 적용 시 주의점

- `.env`의 `MCC/MNC`, 네트워크 주소를 변경해도 일부 고정 IMS 도메인과 호스트 라우트는 자동 변경되지 않습니다. [설정 범위](docs/operations.md#설정-항목과-변경-범위)를 확인합니다.
- `provision`은 실패 시 중단하며, 이전에 성공한 DB 쓰기는 남을 수 있습니다. 원인을 고친 뒤 재실행합니다. SQN 초기화나 볼륨 삭제는 필요하지 않습니다.
- eNB는 원본 설정을 읽기 전용으로 마운트하고 임시 디렉터리의 복사본을 수정합니다. 실행 때 출력되는 runtime 경로로 적용값을 확인할 수 있습니다.
- iFC를 편집한 뒤에는 PyHSS 시작 시 복사, S-CSCF 프로필 갱신, UE 재등록이 필요합니다. `provision`이 자동으로 재시작하지 않습니다. [iFC 적용](docs/operations.md#ifc-변경)을 따릅니다.
- SMSC는 제한된 GSM 7-bit SMS 구현입니다. 한글·이모지와 완전한 상용 SMSC 동작을 지원한다고 가정하지 않습니다.

## 저장소 구조

```text
├── docker-compose.yml       # EPC + IMS + SMSC, 고정 bridge 주소 계획
├── .env.example             # IP, PLMN, RF, 시험 가입자 설정
├── setup_host.sh            # Linux 호스트 설정
├── pyproject.toml           # uv 의존성 및 poe task
├── infrastructure/          # 각 컨테이너 이미지·설정·SMSC 코드
├── scripts/                 # 가입자 등록 / 기본 UE 라우트 보조 스크립트
├── tests/                   # provisioning / eNB 초기화 회귀 검사
└── docs/                    # 구조·변경 이력 / 운영 / 검증
```

## 출처

기반 구성은 [herlesupreeth/docker_open5gs](https://github.com/herlesupreeth/docker_open5gs), 주요 구성요소는 [Open5GS](https://github.com/open5gs/open5gs), [Kamailio](https://github.com/kamailio/kamailio), [PyHSS](https://github.com/nickvsnetworking/pyhss), [srsRAN_4G](https://github.com/srsran/srsRAN_4G), [SoapySDR](https://github.com/pothosware/SoapySDR)입니다. 정확한 고정 버전과 미고정 의존성은 [버전과 재현성](docs/architecture-and-history.md#버전과-재현성)에 정리했습니다. 반입 파일의 기존 저작권·라이선스 고지는 유지합니다.

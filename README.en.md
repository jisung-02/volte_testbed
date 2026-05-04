# volte_testbed

VoLTE testbed infrastructure — an end-to-end 4G/VoLTE testing environment that bundles EPC, RAN, and IMS into a single Docker stack.

Korean: [README.md](./README.md)

## Prerequisites

### Hardware
- SDR: USRP B210 (current default). BladeRF / LimeSDR also supported — adjust `device_name` / `device_args` in `infrastructure/srsenb/enb.conf`.
- USB: For SDR connection. `setup-host` installs the udev rules automatically.

### Software (Docker + uv on the host)

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

## Quick Start

```bash
# First time only: create the env file
cp .env.example .env

# Install poethepoet (no build step thanks to package = false)
uv sync

# One-time host setup (root required)
sudo uv run poe setup-host

# Build base images
uv run poe epc-build

# Build srsenb image (compiles srsRAN_4G — takes 5–15 min)
uv run poe enb-build

# Start 4G EPC + IMS containers
uv run poe epc-run

# Verify all containers are Up
uv run poe epc-status

# Start srsenb (USRP B210)
uv run poe enb-run

# Provision test subscribers
uv run poe provision
```

> A single `setup-host` run handles host sysctl and UE-subnet route persistence (via systemd) at the same time. No separate network-setup task is needed.

## Available Tasks

Run `uv run poe` to list all 12 tasks.

| Category | task | sudo |
|---|---|---|
| eNB | `enb-build`, `enb-run`, `enb-stop`, `enb-logs` | — |
| EPC | `epc-build`, `epc-run`, `epc-stop`, `epc-status`, `epc-logs` | — |
| Subscribers | `provision` | — |
| SMSC | `smsc-test` | — |
| Host setup | `setup-host` | ✅ |

## Environment Variables (`.env`)

After `cp .env.example .env`, adjust to your environment.

### PyHSS REST API

| Variable | Meaning |
|---|---|
| `PYHSS_URL` | PyHSS REST API endpoint (used by the `provision` task to register subscribers) |

### Infrastructure (Docker EPC/IMS)

| Variable | Meaning |
|---|---|
| `MCC`, `MNC`, `TAC` | PLMN ID (`001` / `01` / `1`) |
| `TEST_NETWORK` | Docker bridge subnet (`172.22.0.0/24`) |
| `DOCKER_HOST_IP` | Docker host IP |
| `*_IP` series | Per-container IPs (HSS, MME, SGW, SMF, UPF, PCRF, DNS, RTPENGINE, PYHSS, ICSCF, SCSCF, PCSCF, WEBUI, MONGO, MYSQL, SRS_ENB, SMSC, ENTITLEMENT_SERVER) |
| `UE_IPV4_INTERNET` / `UE_IPV4_IMS` | UE APN subnets |
| `MAX_NUM_UE` | Max number of UEs |

### Test Subscribers

`UE{N}_IMSI`, `UE{N}_KI`, `UE{N}_OPC`, `UE{N}_AMF`, `UE{N}_MSISDN` (N=1..9). The `provision` task stops at the first empty IMSI — defines as many subscribers as you specify within 1..9.

## Troubleshooting

**`sudo: uv: command not found`** — `uv` is installed under the user's PATH (`~/.local/bin`) and sudo cannot find it.
```bash
sudo -E env "PATH=$PATH" uv run poe setup-host
# Or absolute path
sudo $(which uv) run poe setup-host
```

**`docker: permission denied`** — User is not in the docker group. After `setup-host` prints the notice, run `sudo usermod -aG docker $USER` and log out / log back in.

**Container name / network conflict** — If another docker compose stack on the same host uses the same container names (`hss`, `mme`, ..., `pcscf`) or the `docker_open5gs_default` network, the second start fails. Run only one stack at a time.

**UE subnets (`10.10.10.0/24` / `10.20.20.0/24`) unreachable** — `epc-stop` (= `docker compose down`) destroys the docker bridge, which removes the UPF (`172.22.0.8`) next-hop and purges the static routes. After restarting with `epc-run`, re-add the routes:
```bash
sudo systemctl restart volte-testbed-routes
```

**SMS not delivered (sending UE shows send failure)** — SMSC forwarded MT to I-CSCF but recipient is not registered (returns 480). Confirm both UEs are attached first:
```bash
docker exec pcscf kamctl ul show
```
Or check SMSC logs directly:
```bash
docker logs smsc 2>&1 | tail -30
```
If SMSC returned `415 Unsupported Media Type`, the UE sent `text/plain` instead of `application/vnd.3gpp.sms` — UE IMS/SMS config issue.

**SMSC config changes not taking effect** — `default_ifc.xml` is loaded only once when the PyHSS container starts (`pyhss_init.sh:51`). To apply changes:
```bash
docker restart pyhss && docker restart scscf
```
S-CSCF must also be restarted so that the cached subscriber iFC is refreshed. Already-registered UEs may need to re-register (Re-REGISTER, or toggle airplane mode).

**Korean/emoji SMS rejected by SMSC with 400** — This SMSC only supports DCS=0x00 (default 7-bit GSM). UCS-2 (DCS=0x08, required for Korean/emoji) raises `NotImplementedError` → 400. Use plain ASCII SMS for testing.

## Credits / Based On

This testbed is based on the structure of [herlesupreeth/docker_open5gs](https://github.com/herlesupreeth/docker_open5gs) and integrates the following open-source components in Docker:

| Component | Project | Version/Tag |
|---|---|---|
| 4G EPC | [Open5GS](https://github.com/open5gs/open5gs) | commit `47d0062c` |
| IMS (P/I/S-CSCF) | [Kamailio](https://github.com/kamailio/kamailio) | commit `6ce33529` |
| eNodeB (SDR) | [srsRAN_4G](https://github.com/srsran/srsRAN_4G) | `release_23_11` |
| IMS HSS | [PyHSS](https://github.com/nickvsnetworking/pyhss) | tag `1.0.2` |
| SDR abstraction | [SoapySDR](https://github.com/pothosware/SoapySDR) | `soapy-sdr-0.8.1` |
| Media relay | rtpengine | See `infrastructure/rtpengine/Dockerfile` |
| SMS (MO+MT) | self-implementation (Python + smsutil) | `infrastructure/smsc/` |

Base OS image: Ubuntu 22.04 (jammy)

## Project Layout

```
volte_testbed/
├── docker-compose.yml          # EPC + IMS service definitions
├── .env.example                # Environment variable template
├── setup_host.sh               # Host OS setup automation
├── pyproject.toml              # poe task definitions
├── infrastructure/             # Container configs (mme, hss, srsenb, pcscf, smsc, ...)
└── scripts/
    ├── provision_subscribers.py
    └── add_ue_routes.py
```

# volte_testbed

A 4G/VoLTE testbed running **EPC, IMS and an SDR eNB on one PC**, based on [docker_open5gs](https://github.com/herlesupreeth/docker_open5gs). It packages the single-PC configuration with subscriber provisioning, host setup and lifecycle commands. Subsequent commits added a Python SMSC and preservation of the complete SIP Via stack.

The maintainer reports successful **LTE attach, IMS registration, voice calls and SMS on real devices** with the existing baseline. That is separate from automated verification of new changes; see the [validation record](docs/testing.md).

[Korean](README.md)

## Documentation

The detailed operational reference is maintained in Korean:

| Document | Contents |
|---|---|
| [Architecture and history](docs/architecture-and-history.md) | Network diagram, addresses/ports, single-PC assumptions, imported baseline and subsequent commits |
| [Operations](docs/operations.md) | Setup, configuration, startup/restart, provisioning, iFC activation, routes and troubleshooting |
| [Testing and limitations](docs/testing.md) | Regression checks, Linux container checks, hardware acceptance matrix and SMSC limitations |

## Requirements

- Hardware operation: Ubuntu 22.04/24.04, Docker Engine, Compose v2+, uv, SCTP/TUN, systemd and USB access.
- Default SDR: USRP B210. Other SDR dependency/configuration examples are present; they are not additional hardware validation claims.
- Local development: Python 3.12+. Python regression tests can also run on macOS.
- One stack per host: explicit container, network and volume names plus published ports prevent running multiple copies concurrently. Changing the Compose project name alone does not isolate them.

## Quick start

Run from the repository root on the Linux experiment host. Do not overwrite an existing `.env`.

```bash
cp .env.example .env
# Set host addresses and the test SIM's IMSI/KI/OPC/AMF/MSISDN.
uv sync --locked
uv run poe test

# This task invokes sudo internally.
uv run poe setup-host
uv run poe epc-build
uv run poe enb-build
uv run poe epc-run
uv run poe epc-status
```

A running container is not necessarily ready. Check initialization logs and API readiness. Host route setup may fail until the Docker bridge exists; inspect the bridge and rerun route setup after EPC startup.

```bash
docker compose logs --tail=100 mysql pyhss hss mme pcscf icscf scscf
curl --fail --silent --show-error 'http://localhost:8080/apn/list?page=0&page_size=1'
sudo systemctl restart volte-testbed-routes
uv run poe provision

# Starts the real SDR eNodeB.
uv run poe enb-run
uv run poe enb-logs
```

Then verify LTE attach, IMS registration, bidirectional voice and SMS. Provisioning success only confirms management operations, not end-to-end service.

## Commands

| Operation | Command |
|---|---|
| List tasks | `uv run poe` |
| Build EPC/IMS | `uv run poe epc-build` |
| Start/status/logs | `uv run poe epc-run`, `epc-status`, `epc-logs` |
| Build/start/log eNB | `uv run poe enb-build`, `enb-run`, `enb-logs` |
| Provision subscribers | `uv run poe provision` |
| Local regression checks | `uv run poe test` |
| SMSC checks only | `uv run poe smsc-test` |
| Shutdown | `uv run poe enb-stop`, then `uv run poe epc-stop` |
| Host status | `sudo ./setup_host.sh --check` |

The eNB is a separate `docker run` container on the same bridge. Stop it before Compose teardown. Named database volumes are retained; restore/check host UE routes after bringing the bridge back.

## Configuration and behavior

Use unquoted `KEY=value` entries in `.env`; the provisioning parser does not expand shell variables or remove inline comments. UE slots 1–9 may have gaps. AMF defaults to `8000`. KI/OPC must each contain 32 hex characters; AMF must contain four. Use numeric IMSI and MSISDN values.

Provisioning stops on failed writes, invalid responses or ambiguous records and uses actual database APN/AUC IDs. Previous writes may remain: correct the problem and rerun, without resetting SQN or deleting volumes. Existing policy/custom iFC state is preserved as described in the operations guide. Cross-database rollback and concurrent provisioning are not supported.

Provisioning verifies the mounted iFC **source**, but no longer restarts PyHSS automatically. After editing `default_ifc.xml`, restart PyHSS, wait for it to be ready, restart S-CSCF, then re-register the UE. The startup script copies the mounted template into the runtime location.

SMSC code is copied into its image: rebuild and recreate `smsc` after code changes. SIP 1xx responses now keep delivery pending; duplicate MO transactions are suppressed while pending and receive the cached final response for 32 seconds after completion. The cache is in memory and does not survive restart. This is a limited GSM 7-bit implementation; UCS-2/Korean/emoji and a complete commercial SMSC are outside its scope.

eNB configuration is mounted read-only and copied to `/tmp/srsenb.*` inside the container before rendering. The runtime directory holds `enb.conf` and its companion files, leaving experiment inputs unchanged.

Some IMS domains and host routes remain hard-coded to the default PLMN/subnets. `.env` changes alone do not reconfigure every component. See the detailed configuration boundaries before changing address plans.

## Credits

Based on [herlesupreeth/docker_open5gs](https://github.com/herlesupreeth/docker_open5gs), integrating Open5GS, Kamailio, PyHSS, srsRAN_4G, SoapySDR and rtpengine. Pinned versions, unpinned build inputs and source history are recorded in [architecture and history](docs/architecture-and-history.md). Existing copyright and license notices in imported files are retained.

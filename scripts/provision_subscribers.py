#!/usr/bin/env python3
"""
가입자 프로비저닝 스크립트
- Open5GS HSS (MongoDB)
- PyHSS (MySQL) for IMS/VoLTE

사용법: poe provision
"""

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.error import HTTPError


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def check_epc_running() -> bool:
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
    )
    return "hss" in result.stdout.splitlines()


def docker_exec(container: str, *cmd: str) -> tuple[int, str, str]:
    result = subprocess.run(
        ["docker", "exec", container, *cmd],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def _json_request(url: str, method: str, data: dict) -> tuple[int, bytes]:
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except HTTPError as e:
        return e.code, e.read()
    except Exception:
        return 0, b""


def put_json(url: str, data: dict) -> tuple[int, bytes]:
    return _json_request(url, "PUT", data)


def patch_json(url: str, data: dict) -> tuple[int, bytes]:
    return _json_request(url, "PATCH", data)


def get_json(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except HTTPError as e:
        return e.code, e.read()
    except Exception:
        return 0, b""


def upsert_pyhss(
    base_url: str,
    resource: str,
    data: dict,
    lookup_key: str,
    lookup_val: str,
    update_data: dict | None = None,
) -> tuple[int, dict]:
    """Look up before writing; PyHSS 1.0.2 lists return arrays of records.

    page_size=0 is the upstream API's unpaginated mode, suitable for this lab.
    An empty update_data preserves an existing record unchanged.
    """
    status, body = get_json(f"{base_url.rstrip('/')}/{resource}/list?page=0&page_size=0")
    try:
        records = json.loads(body)
    except (ValueError, TypeError):
        records = None
    if status != 200 or not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise RuntimeError(f"PyHSS {resource} lookup failed (HTTP {status}); no write attempted")
    matches = [r for r in records if r.get(lookup_key) == lookup_val]
    if len(matches) > 1:
        raise RuntimeError(f"PyHSS {resource} lookup is ambiguous; resolve duplicate records")
    if matches:
        record = matches[0]
        rid = record.get(f"{resource}_id")
        if type(rid) is not int or rid <= 0:
            raise RuntimeError(f"PyHSS {resource} lookup returned no valid ID")
        patch_data = data if update_data is None else update_data
        if not patch_data:
            return status, record
        status, body = patch_json(f"{base_url.rstrip('/')}/{resource}/{rid}", patch_data)
    else:
        status, body = put_json(f"{base_url.rstrip('/')}/{resource}/", data)
    try:
        record = json.loads(body)
    except (ValueError, TypeError):
        record = None
    rid = record.get(f"{resource}_id") if isinstance(record, dict) else None
    if status not in (200, 201) or type(rid) is not int or rid <= 0:
        raise RuntimeError(f"PyHSS {resource} write failed or returned no valid ID (HTTP {status})")
    return status, record


def validate_subscribers(subscribers: list[dict]) -> None:
    if not subscribers:
        raise ValueError("No subscribers configured")
    seen = set()
    for index, ue in enumerate(subscribers, 1):
        for field, pattern in (("imsi", r"[0-9]{5,15}"), ("ki", r"[0-9a-fA-F]{32}"),
                               ("opc", r"[0-9a-fA-F]{32}"), ("amf", r"[0-9a-fA-F]{4}"),
                               ("msisdn", r"[0-9]{1,15}")):
            value = ue.get(field, "8000" if field == "amf" else "")
            if not isinstance(value, str) or not re.fullmatch(pattern, value):
                raise ValueError(f"Subscriber {index}: invalid {field}")
        if ue["imsi"] in seen:
            raise ValueError(f"Subscriber {index}: duplicate IMSI")
        seen.add(ue["imsi"])


def provision_open5gs(env: dict, subscribers: list[dict]) -> None:
    validate_subscribers(subscribers)
    print("[1/2] Open5GS HSS (MongoDB)")
    print("-" * 40)

    for ue in subscribers:
        imsi = ue["imsi"]
        ki = ue["ki"]
        opc = ue["opc"]
        print(f"  Adding IMSI: {imsi}")

        # Explicit field updates preserve the existing authentication SQN.
        mongo_script = f"""
const imsi = "{imsi}";
const ki = "{ki}";
const opc = "{opc}";
const msisdn = "{ue['msisdn']}";
const amf = "{ue.get('amf', '8000')}";

const defaultSlice = [{{
  sst: 1,
  default_indicator: true,
  session: [
    {{
      name: "internet", type: 3,
      ambr: {{uplink: {{value: 1, unit: 3}}, downlink: {{value: 1, unit: 3}}}},
      qos: {{index: 9, arp: {{priority_level: 8, pre_emption_capability: 1, pre_emption_vulnerability: 1}}}},
      pcc_rule: []
    }},
    {{
      name: "ims", type: 1,
      ambr: {{uplink: {{value: 1, unit: 3}}, downlink: {{value: 1, unit: 3}}}},
      qos: {{index: 5, arp: {{priority_level: 1, pre_emption_capability: 1, pre_emption_vulnerability: 1}}}},
      pcc_rule: []
    }}
  ]
}}];

const existing = db.subscribers.findOne({{imsi}});
if (existing) {{
  // 기존 레코드 — security 필드 교정 + slice 보정
  const slices = Array.isArray(existing.slice) && existing.slice.length > 0
    ? existing.slice
    : defaultSlice;

  if (!Array.isArray(slices[0].session)) slices[0].session = [];

  // IMS APN 없으면 추가
  if (!slices[0].session.some(s => s && s.name === "ims")) {{
    slices[0].session.push({{
      name: "ims", type: 1,
      ambr: {{uplink: {{value: 1, unit: 3}}, downlink: {{value: 1, unit: 3}}}},
      qos: {{index: 5, arp: {{priority_level: 1, pre_emption_capability: 1, pre_emption_vulnerability: 1}}}},
      pcc_rule: []
    }});
  }}

  // security.sqn은 리셋하지 않음 — 리셋 시 LTE AKA 인증 실패
  db.subscribers.updateOne({{imsi}}, {{$set: {{
    "security.k": ki,
    "security.opc": opc,
    "security.amf": amf,
    "msisdn": [msisdn],
    "slice": slices
  }}}});
  print("updated");
}} else {{
  // 신규 생성
  db.subscribers.insertOne({{
    imsi: imsi,
    msisdn: [msisdn],
    security: {{k: ki, opc: opc, amf: amf, sqn: {{low: 0, high: 0, unsigned: false}}}},
    ambr: {{uplink: {{value: 1, unit: 3}}, downlink: {{value: 1, unit: 3}}}},
    slice: defaultSlice,
    access_restriction_data: 32,
    subscriber_status: 0,
    operator_determined_barring: 0,
    network_access_mode: 0,
    subscribed_rau_tau_timer: 12
  }});
  print("inserted");
}}
"""
        rc, _, _ = docker_exec("mongo", "mongosh", "open5gs", "--quiet", "--eval", mongo_script)
        if rc != 0:
            raise RuntimeError("Open5GS MongoDB write failed; provisioning stopped")

    print("  Done\n")


def provision_pyhss(env: dict, subscribers: list[dict]) -> None:
    validate_subscribers(subscribers)
    print("[2/2] PyHSS (IMS)")
    print("-" * 40)

    base_url = env.get("PYHSS_URL", "http://localhost:8080")

    # Keep existing APN policy settings; use database-assigned IDs.
    print("  Creating APNs...")
    _, internet = upsert_pyhss(base_url, "apn", {"apn": "internet", "apn_ambr_dl": 0, "apn_ambr_ul": 0}, "apn", "internet", update_data={})
    _, ims = upsert_pyhss(base_url, "apn", {"apn": "ims", "apn_ambr_dl": 0, "apn_ambr_ul": 0}, "apn", "ims", update_data={})
    print("    APNs ready (internet, ims)")

    mnc = env.get("MNC", "01").zfill(3)
    mcc = env.get("MCC", "001")
    ims_domain = f"ims.mnc{mnc}.mcc{mcc}.3gppnetwork.org"
    scscf_uri = f"sip:scscf.{ims_domain}:6060"

    for ue in subscribers:
        imsi = ue["imsi"]
        ki = ue["ki"]
        opc = ue["opc"]
        msisdn = ue["msisdn"]
        print(f"  Adding IMSI: {imsi} (MSISDN: {msisdn})")

        # AUC 생성 또는 업데이트 (업데이트 시 sqn 리셋 금지 — IMS 인증 깨짐)
        _, auc = upsert_pyhss(
            base_url, "auc",
            {"ki": ki, "opc": opc, "amf": ue.get("amf", "8000"), "sqn": 0, "imsi": imsi},
            "imsi", imsi,
            update_data={"ki": ki, "opc": opc, "amf": ue.get("amf", "8000"), "imsi": imsi},
        )
        auc_id = auc["auc_id"]

        # Subscriber 생성 또는 업데이트
        upsert_pyhss(
            base_url, "subscriber",
            {
                "imsi": imsi,
                "enabled": True,
                "auc_id": auc_id,
                "default_apn": internet["apn_id"],
                "apn_list": f"{internet['apn_id']},{ims['apn_id']}",
                "msisdn": msisdn,
                "ue_ambr_dl": 0,
                "ue_ambr_ul": 0,
            },
            "imsi", imsi,
            update_data={"imsi": imsi, "auc_id": auc_id, "msisdn": msisdn,
                         "default_apn": internet["apn_id"],
                         "apn_list": f"{internet['apn_id']},{ims['apn_id']}"},
        )

        # IMS Subscriber 생성 또는 업데이트
        # ifc_path 필수 — null이면 S-CSCF MAR에서 403 반환
        scscf_peer = f"scscf.{ims_domain}"
        upsert_pyhss(
            base_url, "ims_subscriber",
            {
                "imsi": imsi,
                "msisdn": msisdn,
                "scscf_peer": scscf_peer,
                "msisdn_list": f"[{msisdn}]",
                "ifc_path": "default_ifc.xml",
                "scscf": scscf_uri,
                "scscf_realm": ims_domain,
            },
            "imsi", imsi,
            update_data={"imsi": imsi, "msisdn": msisdn, "msisdn_list": f"[{msisdn}]"},
        )

    print("  Done\n")


def apply_pyhss_ifc_template(project_root: Path) -> None:
    """Verify the source mount; activation remains an explicit operator action."""
    repo_ifc = project_root / "infrastructure" / "pyhss" / "default_ifc.xml"
    if not repo_ifc.is_file():
        raise RuntimeError("Repository iFC template is missing")
    rc, out, _ = docker_exec("pyhss", "cat", "/mnt/pyhss/default_ifc.xml")
    if rc != 0:
        raise RuntimeError("Cannot read PyHSS iFC source mount")
    if out != repo_ifc.read_text():
        raise RuntimeError("PyHSS iFC source mount differs from repository; sync the template first")
    print("  iFC source mount verified; provisioning does not activate template changes.")
    print("  After an iFC edit: docker restart pyhss && docker restart scscf")
    print("  Then re-REGISTER the UE to apply the new iFC.\n")


def main() -> None:
    project_root = Path(__file__).parent.parent
    env_file = project_root / ".env"

    if not env_file.exists():
        print("Error: .env file not found")
        sys.exit(1)

    env = load_env(env_file)

    if not check_epc_running():
        print("Error: EPC is not running")
        print("Run first: poe epc-run")
        sys.exit(1)

    # 가입자 목록 구성
    subscribers = []
    for idx in range(1, 10):
        imsi = env.get(f"UE{idx}_IMSI", "")
        if not imsi:
            if any(env.get(f"UE{idx}_{field}", "") for field in ("KI", "OPC", "MSISDN")):
                raise ValueError(f"UE{idx}: IMSI missing")
            continue
        subscribers.append(
            {
                "imsi": imsi,
                "ki": env.get(f"UE{idx}_KI", ""),
                "opc": env.get(f"UE{idx}_OPC", ""),
                "amf": env.get(f"UE{idx}_AMF", "8000"),
                "msisdn": env.get(f"UE{idx}_MSISDN", ""),
            }
        )

    if not subscribers:
        print("Error: No subscribers defined in .env (UE1_IMSI, UE2_IMSI, ...)")
        sys.exit(1)

    print("=" * 40)
    print("Subscriber Provisioning")
    print("=" * 40)
    print(f"Found {len(subscribers)} subscriber(s) in .env\n")

    provision_open5gs(env, subscribers)
    provision_pyhss(env, subscribers)
    apply_pyhss_ifc_template(project_root)

    print("=" * 40)
    print("Provisioning Complete!")
    print("=" * 40)
    print()
    print("Subscribers:")
    for ue in subscribers:
        print(f"  IMSI: {ue['imsi']}, MSISDN: {ue['msisdn']}")
    print()
    print("Verify:")
    print("  Open5GS WebUI: http://localhost:9999")
    print("  PyHSS API:     http://localhost:8080/docs/")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as error:
        print(f"Error: {error}. Earlier writes may have completed; fix the problem and rerun.", file=sys.stderr)
        sys.exit(1)
    except OSError:
        print("Error: cannot access a required file or Docker executable; check local setup.", file=sys.stderr)
        sys.exit(1)

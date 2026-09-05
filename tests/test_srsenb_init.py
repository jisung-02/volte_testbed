"""Optional Linux-container check; no SDR, privileges, or network required."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(os.environ.get("RUN_CONTAINER_TESTS") != "1", reason="set RUN_CONTAINER_TESTS=1 to run Docker check")
@pytest.mark.parametrize("source_path", ["/etc/srsran", "/root/.config/srsran"])
def test_init_renders_runtime_config_without_mutating_source(tmp_path, source_path):
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "config"
    shutil.copytree(root / "infrastructure/srsenb", source)
    original = {p.name: p.read_bytes() for p in source.iterdir()}
    stub = tmp_path / "srsenb"
    stub.write_text('''#!/bin/bash
set -e
test "$1" = "$PWD/enb.conf"
test "$2" = "--test-argument"
grep -q '^mcc = 999$' "$1"
grep -q '^tx_gain = 42$' "$1"
grep -q 'tac = 0x000a' rr.conf
grep -q 'dl_earfcn = 1850' rr.conf
test -f sib.conf && test -f rb.conf
echo runtime-config-ok
''')
    stub.chmod(0o755)
    result = subprocess.run([
        "docker", "run", "--rm", "--network", "none",
        "-v", f"{source}:{source_path}:ro",
        "-v", f"{root / 'infrastructure/srslte/srslte_init.sh'}:/init.sh:ro",
        "-v", f"{stub}:/usr/local/bin/srsenb:ro",
        "-e", "COMPONENT_NAME=enb", "-e", "MCC=999", "-e", "MNC=99",
        "-e", "MME_IP=172.22.0.9", "-e", "TAC=10",
        "-e", "SRSENB_TX_GAIN=42", "-e", "SRSENB_DL_EARFCN=1850",
        "ubuntu:22.04", "bash", "/init.sh", "--test-argument",
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "runtime-config-ok" in result.stdout
    assert {p.name: p.read_bytes() for p in source.iterdir()} == original

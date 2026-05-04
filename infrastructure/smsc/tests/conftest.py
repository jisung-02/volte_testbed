"""pytest configuration for SMSC tests.

Import-path fix: smsc.py uses bare ``from sip import ...`` / ``from tpdu import ...``
so that it can be run directly via ``python smsc.py`` inside the Docker container
(where /app/ holds sip.py and tpdu.py flat alongside smsc.py).

When pytest loads smsc.py as ``infrastructure.smsc.smsc``, the bare imports
would otherwise fail because ``sip`` is not a top-level module from the
``volte_testbed/`` rootdir.  Inserting ``infrastructure/smsc/`` at the front
of sys.path makes ``sip`` and ``tpdu`` visible as top-level names, matching
the Docker flat-layout expectation while keeping the package-style test imports
(``from infrastructure.smsc.smsc import SmscHandler``) intact.
"""
import sys
from pathlib import Path

# Put volte_testbed/infrastructure/smsc/ at the front of sys.path so that
# bare ``import sip`` / ``import tpdu`` inside smsc.py resolve to the real
# modules during pytest collection.
sys.path.insert(0, str(Path(__file__).parent.parent))

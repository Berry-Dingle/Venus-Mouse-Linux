"""Where the tests find saved evidence (profiles, macros, captures, device reads).

* Inside the full development workspace (the repository's parent folder holds
  ``usb-captures/`` and ``ProfilesMacros/``), the tests use all of it.
* In a plain clone of the repository they use ``tests/evidence/``: a small
  copy of the workspace layout with the Windows-saved test profiles/macros,
  the factory profiles, the factory memory read and the DayZ profile. Tests
  that need anything else (saved live reads, USB captures, investigation
  files) are skipped there, not failed (see ``conftest.py``).
* ``VENUS_EVIDENCE_ROOT`` overrides both.
"""

from __future__ import annotations

import os
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent                      # the repository (linux-suite-prototype)
WORKSPACE = REPO.parent
BUNDLED = TESTS / "evidence"


def evidence_root() -> Path:
    override = os.environ.get("VENUS_EVIDENCE_ROOT")
    if override:
        return Path(override).resolve()
    if (WORKSPACE / "usb-captures").is_dir() and (WORKSPACE / "ProfilesMacros").is_dir():
        return WORKSPACE
    return BUNDLED


EVIDENCE = evidence_root()


def using_bundled_evidence() -> bool:
    return EVIDENCE == BUNDLED

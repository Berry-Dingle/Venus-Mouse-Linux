"""Shared test helper: a temporary workspace seeded with sample macros.

The samples are the Windows-saved test macros in the bundled evidence
(``tests/evidence/ProfilesMacros``), which every checkout has.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

from evidence_paths import BUNDLED
from venus_profile.app_store import AppStore
from venus_profile.workspace import VenusWorkspace

SAMPLE_DIR = BUNDLED / "ProfilesMacros"
SAMPLE_MACROS = (
    "01_Key1_Once.MSDB", "02_Keys12_Once.MSDB", "03_Key1_Delay5s_Key2.MSDB",
    "04_Key1_Repeat3.MSDB", "05_Key1_Repeat3_5s.MSDB", "06_Key1_WhileHeld_5s.MSDB",
    "07_Key1_Toggle_5s.MSDB", "08_Keys12_DefDelay.MSDB", "09_Keys12_RecDelay.MSDB",
    "10_Keys12_NoDelay.MSDB", "11_Ctrl1.MSDB", "12_MouseLeftClick.MSDB",
    "13_MouseRightClick.MSDB", "15_Key1_LClick_Key2.MSDB",
    "POE-Flasks.MSMACRO", "Aura-Switch.MSMACRO",
)


def _sample(name: str) -> Path:
    return SAMPLE_DIR / name if name.endswith(".MSMACRO") else SAMPLE_DIR / "TestProfiles" / name


def demo_workspace(root: str | Path | None = None, *, saved: bool = True) -> VenusWorkspace:
    if root is None:
        base = os.environ.get("VENUS_PROFILE_DATA_DIR") or tempfile.mkdtemp()
        root = Path(tempfile.mkdtemp(dir=Path(base).parent if Path(base).parent.exists() else None))
    workspace = VenusWorkspace.open(AppStore(root))
    staging = Path(tempfile.mkdtemp(dir=root))
    for name in SAMPLE_MACROS:
        (staging / name).write_bytes(_sample(name).read_bytes())
        workspace.library.import_file(staging / name)
    by_name = {entry.name: entry for entry in workspace.library.entries}
    workspace.load_macro_into_slot(by_name["POE-Flasks"].macro_id, 0)
    workspace.load_macro_into_slot(by_name["Aura-Switch"].macro_id, 1)
    workspace.assign_macro_to_button(0, 1, 7)
    workspace.assign_macro_to_button(1, 1, 18)
    if saved:
        workspace.save_all()
    return workspace

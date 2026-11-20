from pathlib import Path

import pytest

from venus_profile.actions import ActionRecord as A
from venus_profile.macro import MacroFile


def official(controlled: Path, name: str) -> MacroFile:
    return MacroFile.from_path(controlled / f"{name}.MSDB")


def assert_generated_matches(
    controlled: Path,
    name: str,
    actions: list[A],
    *,
    loop_time: int = 1,
    loop_type: int = 2,
    delay_type: int = 2,
    default_delay_ms: int = 10,
    count: int | None = None,
    trailing_records: list[A] | None = None,
):
    expected = official(controlled, name)
    generated = MacroFile.create(
        actions,
        macro_file_path=expected.macro_file_path,
        loop_time=loop_time,
        loop_type=loop_type,
        delay_type=delay_type,
        default_delay_ms=default_delay_ms,
        count=count,
        trailing_records=trailing_records or [],
    )
    assert generated.count == expected.count
    assert generated.loop_time == expected.loop_time
    assert generated.loop_type == expected.loop_type
    assert generated.delay_type == expected.delay_type
    assert generated.default_delay_ms == expected.default_delay_ms
    assert generated.action_struct.encode(preserve_text=False) == (
        expected.action_struct.encode(preserve_text=False)
    )


def test_generate_key_1_once(controlled):
    assert_generated_matches(
        controlled, "01_Key1_Once",
        [A.keyboard(0x1E, True), A.delay(10), A.keyboard(0x1E, False)],
    )


def test_generate_keys_1_and_2(controlled):
    actions = [
        A.keyboard(0x1E, True), A.delay(10), A.keyboard(0x1E, False), A.delay(10),
        A.keyboard(0x1F, True), A.delay(10), A.keyboard(0x1F, False),
    ]
    assert_generated_matches(controlled, "02_Keys12_Once", actions, delay_type=1)


def test_generate_keys_1_through_5(project_root):
    expected = MacroFile.from_path(project_root / "ProfilesMacros" / "POE-Flasks.MSMACRO")
    actions = []
    for usage in range(0x1E, 0x23):
        actions.extend([A.keyboard(usage, True), A.delay(10), A.keyboard(usage, False)])
        if usage != 0x22:
            actions.append(A.delay(10))
    generated = MacroFile.create(
        actions,
        macro_file_path=expected.macro_file_path,
        loop_time=1, loop_type=0, delay_type=2, default_delay_ms=1,
    )
    assert generated.action_struct.encode(preserve_text=False) == expected.action_struct.encode(
        preserve_text=False)


def test_generate_ctrl_qwert(project_root):
    expected = MacroFile.from_path(project_root / "ProfilesMacros" / "Aura-Switch.MSMACRO")
    actions = [A.keyboard(0xE0, True), A.delay(10)]
    for usage in (0x14, 0x1A, 0x08, 0x15, 0x17):
        actions.extend([A.keyboard(usage, True), A.delay(10),
                        A.keyboard(usage, False), A.delay(10)])
    actions.append(A.keyboard(0xE0, False))
    generated = MacroFile.create(
        actions,
        macro_file_path=expected.macro_file_path,
        loop_time=1, loop_type=2, delay_type=2, default_delay_ms=10,
    )
    assert generated.action_struct.encode(preserve_text=False) == expected.action_struct.encode(
        preserve_text=False)


@pytest.mark.parametrize(
    ("name", "mask"),
    [("12_MouseLeftClick", 1), ("13_MouseRightClick", 2)],
)
def test_generate_mouse_clicks(controlled, name, mask):
    actions = [A.mouse_button(mask, True), A.mouse_button(mask, False)]
    if name == "12_MouseLeftClick":
        assert_generated_matches(
            controlled, name, actions, delay_type=0, count=8,
            trailing_records=[A.zero()] * 6,
        )
    else:
        assert_generated_matches(controlled, name, actions)


def test_generate_mixed_keyboard_mouse(controlled):
    actions = [
        A.keyboard(0x1E, True), A.delay(10),
        A.mouse_button(1, True), A.mouse_button(1, False),
        A.keyboard(0x1E, False), A.delay(10),
        A.keyboard(0x1F, True), A.delay(10), A.keyboard(0x1F, False),
    ]
    assert_generated_matches(controlled, "15_Key1_LClick_Key2", actions)


@pytest.mark.parametrize(
    ("name", "loop_type", "loop_time"),
    [
        ("04_Key1_Repeat3", 0, 3),
        ("06_Key1_WhileHeld_5s", 1, 1),
        ("07_Key1_Toggle_5s", 3, 1),
    ],
)
def test_generate_loop_modes(controlled, name, loop_type, loop_time):
    if name == "04_Key1_Repeat3":
        actions = [A.keyboard(0x1E, True), A.delay(10), A.keyboard(0x1E, False)]
        delay_type = 2
    else:
        actions = [A.keyboard(0x1E, True), A.keyboard(0x1E, False), A.delay(5000)]
        delay_type = 1 if loop_type == 1 else 2
    assert_generated_matches(
        controlled, name, actions,
        loop_type=loop_type, loop_time=loop_time, delay_type=delay_type,
    )


def test_generate_5000_ms_delay(controlled):
    actions = [
        A.keyboard(0x1E, True), A.keyboard(0x1E, False), A.delay(5000),
        A.keyboard(0x1F, True), A.keyboard(0x1F, False),
    ]
    assert A.delay(5000).raw == bytes.fromhex("06F401")
    assert_generated_matches(controlled, "03_Key1_Delay5s_Key2", actions, delay_type=1)

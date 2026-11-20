import pytest

from venus_profile.actions import ACTION_DELAY_MAX_MS, ActionRecord
from venus_profile.macro import MacroFile
from venus_profile.validation import validate_macro


def new_macro(*actions, default_inserted_delay_ms=10):
    return MacroFile.create(
        actions,
        macro_file_path="DelayTest.MSMACRO",
        default_inserted_delay_ms=default_inserted_delay_ms,
    )


def issue_codes(macro):
    return [issue.code for issue in validate_macro(macro)]


def test_default_inserted_delay_500_ms_is_accepted():
    macro = new_macro(default_inserted_delay_ms=500)
    assert macro.default_inserted_delay_ms == 500
    assert macro.records == []


def test_default_inserted_delay_999_ms_is_accepted():
    macro = new_macro()
    macro.set_default_inserted_delay_ms(999)
    assert macro.default_inserted_delay_ms == 999


def test_default_inserted_delay_1000_ms_is_rejected():
    macro = new_macro()
    with pytest.raises(ValueError, match="between 0 and 999"):
        macro.set_default_inserted_delay_ms(1000)


def test_validator_rejects_out_of_range_default_inserted_delay():
    macro = new_macro()
    macro.document.set("Setting", "DefDelayTime", "1000")
    assert "default-inserted-delay-range" in issue_codes(macro)


def test_action_delay_500_ms_encoding():
    assert ActionRecord.action_delay_ms(500).raw == bytes.fromhex("063200")


def test_action_delay_5000_ms_encoding():
    assert ActionRecord.action_delay_ms(5000).raw == bytes.fromhex("06F401")


def test_action_delay_655350_ms_is_accepted():
    record = ActionRecord.action_delay_ms(655350)
    assert ACTION_DELAY_MAX_MS == 655350
    assert record.raw == bytes.fromhex("06FFFF")
    assert record.delay_ms == 655350


def test_action_delay_above_655350_ms_is_rejected():
    with pytest.raises(ValueError, match="655350"):
        ActionRecord.action_delay_ms(655360)


def test_action_delay_not_divisible_by_10_ms_is_rejected():
    with pytest.raises(ValueError, match="multiple of 10"):
        ActionRecord.action_delay_ms(999)


def test_round_trip_preserves_default_and_action_delay_independently():
    original = new_macro(
        ActionRecord.action_delay_ms(5000),
        default_inserted_delay_ms=500,
    )
    reparsed = MacroFile.from_bytes(original.to_bytes())
    assert reparsed.default_inserted_delay_ms == 500
    assert reparsed.records[0].delay_ms == 5000
    assert reparsed.to_bytes() == original.to_bytes()

    reparsed.set_default_inserted_delay_ms(999)
    assert reparsed.default_inserted_delay_ms == 999
    assert reparsed.records[0].raw == bytes.fromhex("06F401")

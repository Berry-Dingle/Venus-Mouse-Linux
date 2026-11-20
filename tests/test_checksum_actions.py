import pytest

from venus_profile.actions import ActionRecord, ActionType
from venus_profile.checksum import ProfileStruct, additive_checksum


def test_additive_checksum():
    assert additive_checksum(bytes([0x84, 0x1E, 0, 6, 1])) == 0xA9


def test_profile_struct_round_trip():
    source = "841E00060100041E00CB"
    blob = ProfileStruct.decode(source)
    assert blob.valid
    assert blob.encode() == source
    assert ProfileStruct.from_data(blob.data).encode(preserve_text=False) == source


@pytest.mark.parametrize(
    ("record", "kind", "description"),
    [
        (ActionRecord.keyboard(0x1E, True), ActionType.KEYBOARD, "keyboard down"),
        (ActionRecord.keyboard(0x1E, False), ActionType.KEYBOARD, "keyboard up"),
        (ActionRecord.delay(5000), ActionType.DELAY, "delay 5000"),
        (ActionRecord.mouse_button(1, True), ActionType.MOUSE_BUTTON, "mouse left down"),
        (ActionRecord.mouse_button(2, False), ActionType.MOUSE_BUTTON, "mouse right up"),
        (ActionRecord.zero(), ActionType.ZERO, "zero/padding"),
    ],
)
def test_action_constructors(record, kind, description):
    assert record.action_type == kind
    assert description in record.describe()


def test_delay_limits_and_quantization():
    assert ActionRecord.delay(655350).raw == b"\x06\xff\xff"
    with pytest.raises(ValueError):
        ActionRecord.delay(5)
    with pytest.raises(ValueError):
        ActionRecord.delay(655360)


def test_unknown_record_is_lossless():
    record = ActionRecord(b"\x7f\xaa\x55")
    assert record.action_type == ActionType.UNKNOWN
    assert record.raw == b"\x7f\xaa\x55"

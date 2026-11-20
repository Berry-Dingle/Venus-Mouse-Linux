from venus_profile.cli import main
from venus_profile.actions import ActionRecord
from venus_profile.macro import MacroFile
from venus_profile.macroset import MacroSet, report4_address
from venus_profile.pfd import PfdProfile
from venus_profile.validation import validate_macro, validate_macroset, validate_pfd


def codes(issues):
    return [issue.code for issue in issues]


def test_complete_macro_validates(controlled):
    macro = MacroFile.from_path(controlled / "01_Key1_Once.MSDB")
    assert validate_macro(macro) == []


def test_mouse_count_anomaly_is_preserved_and_warned(controlled):
    macro = MacroFile.from_path(controlled / "12_MouseLeftClick.MSDB")
    issues = validate_macro(macro)
    assert codes(issues).count("zero-within-count") == 6
    assert not any(issue.severity == "error" for issue in issues)
    assert macro.to_bytes() == (controlled / "12_MouseLeftClick.MSDB").read_bytes()


def test_truncated_macro_reports_missing_blob(controlled):
    macro = MacroFile.from_path(controlled / "MacroDB" / "10_Keys12_NoDelay.MSMACRO")
    assert "missing-field" in codes(validate_macro(macro))


def test_macroset_validates(controlled):
    macro_set = MacroSet.from_path(controlled / "MacroDB" / "MacroSet.MSDB")
    assert validate_macroset(macro_set) == []
    assert report4_address(0) == 0x0478
    assert report4_address(1) == 0x0540
    assert report4_address(14) == 0x0F68


def test_complete_pfd_slot_relationship(controlled):
    profile = PfdProfile.from_path(controlled / "15_Key1_LClick_Key2.pfd")
    macro_set = MacroSet.from_path(controlled / "MacroDB" / "MacroSet.MSDB")
    assert validate_pfd(profile, macro_set=macro_set,
                        macro_dir=controlled / "MacroDB") == []


def test_truncated_pfd_is_rejected_but_round_trips(controlled):
    path = controlled / "04_Key1_Repeat3.pfd"
    profile = PfdProfile.from_path(path)
    assert "button-count" in codes(validate_pfd(profile))
    assert profile.to_bytes() == path.read_bytes()


def test_cli_inspect_validate_decode(controlled, capsys):
    macro = controlled / "01_Key1_Once.MSDB"
    assert main(["inspect", str(macro)]) == 0
    assert "Count: 3" in capsys.readouterr().out
    assert main(["validate", str(macro)]) == 0
    assert "valid" in capsys.readouterr().out
    assert main(["decode-macro", str(macro)]) == 0
    output = capsys.readouterr().out
    assert "keyboard down usage=0x1E" in output
    assert "delay 10 ms" in output


def test_validator_reports_unknown_record():
    macro = MacroFile.create(
        [ActionRecord(b"\x7f\xaa\x55")],
        macro_file_path="Unknown.MSMACRO",
    )
    assert "unknown-record" in codes(validate_macro(macro))


def test_validator_reports_missing_release():
    macro = MacroFile.create(
        [ActionRecord.keyboard(0x1E, True)],
        macro_file_path="Held.MSMACRO",
    )
    issues = validate_macro(macro)
    assert "missing-key-release" in codes(issues)
    assert any(issue.severity == "error" for issue in issues)


def test_validator_rejects_count_over_capacity(controlled):
    macro = MacroFile.from_bytes((controlled / "01_Key1_Once.MSDB").read_bytes())
    macro.document.set("Setting", "Count", "64")
    assert "count-range" in codes(validate_macro(macro))

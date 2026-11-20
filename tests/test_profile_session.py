import pytest

from venus_profile.profile_session import (
    ProfileDocumentSession,
    ProfileLoadError,
    ProfileSaveError,
)
from venus_profile.pfd import PollingRateHz


def test_load_validates_and_preserves_unknown_fields(controlled, tmp_path):
    source = controlled / "00_Baseline_NoMacro.pfd"
    raw = source.read_bytes().replace(b"PollingRate=2", b"PollingRate=3")
    selected = tmp_path / "unknown-value.pfd"
    selected.write_bytes(raw)

    session = ProfileDocumentSession.load(selected)

    assert not session.dirty
    assert any(issue.code == "unknown-polling-rate" for issue in session.issues)
    assert session.profile.polling_rate_raw == 3
    assert session.profile.to_bytes() == raw


def test_in_memory_edit_and_save_as_leave_source_unchanged(controlled, tmp_path):
    source = controlled / "00_Baseline_NoMacro.pfd"
    original = source.read_bytes()
    session = ProfileDocumentSession.load(source)
    session.profile.set_polling_rate_hz(PollingRateHz.HZ_1000)

    assert session.dirty
    destination = tmp_path / "nested" / "edited.pfd"
    assert session.save_as(destination) == destination
    assert destination.read_bytes() == session.profile.to_bytes()
    assert source.read_bytes() == original
    assert not session.dirty
    assert session.path == destination


def test_load_rejects_invalid_pfd(tmp_path):
    selected = tmp_path / "invalid.pfd"
    selected.write_bytes(b"[OTHER]\nValue=1\n")
    with pytest.raises(ProfileLoadError, match="Invalid PFD"):
        ProfileDocumentSession.load(selected)


def test_save_as_never_overwrites_existing_destination(controlled, tmp_path):
    session = ProfileDocumentSession.load(controlled / "00_Baseline_NoMacro.pfd")
    destination = tmp_path / "existing.pfd"
    destination.write_bytes(b"keep")

    with pytest.raises(ProfileSaveError, match="already exists"):
        session.save_as(destination)
    assert destination.read_bytes() == b"keep"


def test_save_as_reports_io_errors(controlled, tmp_path):
    session = ProfileDocumentSession.load(controlled / "00_Baseline_NoMacro.pfd")
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")

    with pytest.raises(ProfileSaveError, match="Could not save"):
        session.save_as(blocker / "file.pfd")

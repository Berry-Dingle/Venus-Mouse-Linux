from pathlib import Path
import re

import pytest

from evidence_paths import EVIDENCE, WORKSPACE, using_bundled_evidence


@pytest.fixture(scope="session")
def project_root() -> Path:
    """The evidence root: the development workspace, or ``tests/evidence`` in a plain clone."""
    return EVIDENCE


#: Workspace folders that hold evidence (also where some modules' default paths point).
_EVIDENCE_FOLDERS = ("usb-captures", "investigation", "ProfilesMacros", "device-baseline")
_PATH_PATTERN = re.compile(r"(/[^\s'\"\]\[,:]+)")


def _evidence_roots() -> list[Path]:
    roots = [EVIDENCE]
    roots += [WORKSPACE / name for name in _EVIDENCE_FOLDERS]   # module defaults point beside the repo
    return roots


def _candidate_paths(exc: BaseException):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        for value in (getattr(exc, "filename", None), *getattr(exc, "args", ()), getattr(exc, "cmd", None)):
            items = value if isinstance(value, (list, tuple)) else [value]
            for item in items:
                if isinstance(item, (str, Path)):
                    yield from _PATH_PATTERN.findall(str(item))
        yield from _PATH_PATTERN.findall(str(exc))
        exc = exc.__cause__ or exc.__context__


def _missing_evidence(exc: BaseException) -> str | None:
    """A missing evidence path named by ``exc``, in a plain clone only; else None (a real failure)."""
    if not using_bundled_evidence():
        return None
    for text in _candidate_paths(exc):
        path = Path(text)
        for root in _evidence_roots():
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if not path.exists():
                return str(path)
    return None


def _skip_if_missing_evidence(exc: BaseException):
    missing = _missing_evidence(exc)
    if missing is not None:
        pytest.skip(f"evidence not included in this checkout: {missing}")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    try:
        return (yield)
    except Exception as exc:  # noqa: BLE001 - re-raised unless it names missing evidence
        _skip_if_missing_evidence(exc)
        raise


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    try:
        return (yield)
    except Exception as exc:  # noqa: BLE001 - re-raised unless it names missing evidence
        _skip_if_missing_evidence(exc)
        raise


@pytest.fixture(scope="session")
def controlled(project_root: Path) -> Path:
    return project_root / "ProfilesMacros" / "TestProfiles"


@pytest.fixture(autouse=True)
def isolated_app_store(tmp_path, monkeypatch):
    """Never let a test read or write the user's real application store."""
    store = tmp_path / "venus-app-store"
    monkeypatch.setenv("VENUS_PROFILE_DATA_DIR", str(store))
    # Named-profile library (normally ~/Documents/VenusMouse/Profiles).
    monkeypatch.setenv("VENUS_PROFILE_LIBRARY_DIR", str(tmp_path / "venus-profile-library"))
    # Macro library (normally ~/Documents/VenusMouse/Macros).
    monkeypatch.setenv("VENUS_MACRO_LIBRARY_DIR", str(tmp_path / "venus-macro-library"))
    # Never launch the real mouse-reading helper from a test (GUI read tests
    # opt back in and inject a fake reader).
    monkeypatch.setenv("VENUS_PROFILE_NO_DEVICE_READ", "1")
    # Never launch the real lighting writer either, even in tests that opt
    # back in to reading (launcher tests unset it and fake the process).
    monkeypatch.setenv("VENUS_PROFILE_NO_DEVICE_WRITE", "1")
    return store

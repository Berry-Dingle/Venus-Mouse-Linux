"""The deb: one Architecture: all package with the shared payload and a minimal postinst."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess

import pytest

PROJECT = Path(__file__).parents[1]
DEB_DIR = PROJECT / "packaging" / "deb"


def _builder():
    spec = importlib.util.spec_from_file_location("deb_build", DEB_DIR / "build_deb.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fields(text: str) -> dict[str, str]:
    fields = {}
    for line in text.splitlines():
        if line and not line.startswith(" ") and ":" in line:
            key, _, value = line.partition(":")
            fields[key] = value.strip()
    return fields


def test_control_is_architecture_all_with_version_independent_python():
    builder = _builder()
    fields = _fields(builder.control_text(6000))
    assert fields["Package"] == "venus-mouse"
    assert fields["Architecture"] == "all"
    assert fields["Version"] == f"{builder.project_version()}-1"
    assert fields["Depends"] == ("python3 (>= 3.11), python3-gi, gir1.2-gtk-4.0 (>= 4.10), "
                                 "gir1.2-adw-1 (>= 1.5), hicolor-icon-theme")
    assert "python3.1" not in fields["Depends"] and "python3:any" not in fields["Depends"]
    assert fields["Homepage"] == "https://github.com/Berry-Dingle/Venus-Mouse-Linux"
    assert fields["Installed-Size"] == "6000"


def test_postinst_only_creates_the_group_and_reloads_rules():
    postinst = DEB_DIR / "postinst"
    assert subprocess.run(["sh", "-n", str(postinst)]).returncode == 0
    text = postinst.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    code = code.replace(">/dev/null", "")          # output redirection, not device access
    assert 'if [ "$1" = "configure" ]' in code
    assert "systemd-sysusers venus-mouse.conf" in code and "addgroup --system venus-mouse" in code
    assert "udevadm control --reload-rules" in code
    for forbidden in ("usermod", "useradd", "adduser ", "gpasswd", "trigger", "sudo", "/dev/", "hidraw",
                      "chmod", "chown", "rm "):
        assert forbidden not in code, forbidden


def test_copyright_is_dep5_with_both_licences():
    text = _builder().copyright_text()
    assert text.startswith("Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n")
    assert "License: GPL-3.0+\n" in text and "/usr/share/common-licenses/GPL-3" in text
    assert "License: OFL-1.1\n" in text and "SIL OPEN FONT LICENSE Version 1.1" in text
    assert "Files: usr/share/venus-mouse/venus_profile/gui/data/fonts/*" in text
    body = text.split("License: OFL-1.1\n", 2)[-1]
    assert all(line.startswith(" ") for line in body.splitlines())      # continuation lines only


@pytest.mark.skipif(shutil.which("dpkg-deb") is None or shutil.which("git") is None,
                    reason="dpkg-deb or git not installed")
def test_built_deb_is_reproducible_and_holds_the_shared_payload(tmp_path):
    builder = _builder()
    first = builder.build(tmp_path / "a")
    second = builder.build(tmp_path / "b")
    assert first.name == f"venus-mouse_{builder.deb_version()}_all.deb"
    assert first.read_bytes() == second.read_bytes()

    def dpkg(*args):
        return subprocess.run(["dpkg-deb", *args, str(first)], capture_output=True, text=True,
                              check=True).stdout

    assert _fields(dpkg("--field"))["Architecture"] == "all"
    listing = dpkg("--contents")
    assert all(line.split()[1] == "root/root" for line in listing.splitlines())   # no builder account
    files = sorted(line.split()[-1].removeprefix("./") for line in listing.splitlines()
                   if not line.startswith("d"))
    payload = builder.install_tree.install(tmp_path / "tree")
    docs = ["usr/share/doc/venus-mouse/README.md", "usr/share/doc/venus-mouse/changelog.Debian.gz",
            "usr/share/doc/venus-mouse/copyright"]
    assert files == sorted(payload + docs)
    assert not [f for f in files if f.endswith((".pyc", ".pyo")) or "__pycache__" in f]

    extracted = tmp_path / "x"
    subprocess.run(["dpkg-deb", "--raw-extract", str(first), str(extracted)], check=True)
    sums = (extracted / "DEBIAN/md5sums").read_text(encoding="utf-8").splitlines()
    assert sorted(line.split("  ", 1)[1] for line in sums) == files
    for line in sums:
        digest, name = line.split("  ", 1)
        assert hashlib.md5((extracted / name).read_bytes()).hexdigest() == digest
    assert (extracted / "DEBIAN/postinst").read_bytes() == (DEB_DIR / "postinst").read_bytes()
    assert (extracted / "DEBIAN/postinst").stat().st_mode & 0o777 == 0o755
    assert not (extracted / "DEBIAN/conffiles").exists()

# Venus Mouse: Linux configuration utility for the UtechSmart Venus MMO mouse.
#
# One noarch package for every Fedora release: the app is installed as an
# application's private modules in /usr/share/venus-mouse (not site-packages)
# and run by /usr/bin/python3, so the package does not depend on a particular
# Python version (no python(abi) requirement, no .pyc files).
# Build with packaging/rpm/build_rpm.py (see packaging/rpm/README.md).

%global app_id io.github.berry_dingle.VenusMouse

Name:           venus-mouse
Version:        0.2.3
Release:        1%{?dist}
Summary:        Configure buttons, DPI, lighting and macros for the UtechSmart Venus mouse
# The app is GPL-3.0-or-later; the bundled Alex Brush font is OFL-1.1.
License:        GPL-3.0-or-later AND OFL-1.1
URL:            https://github.com/Berry-Dingle/Venus-Mouse-Linux
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3
BuildRequires:  desktop-file-utils
BuildRequires:  systemd-rpm-macros

Requires:       python3 >= 3.11
Requires:       python3-gobject
Requires:       gtk4 >= 4.10
Requires:       libadwaita >= 1.5
Requires:       hicolor-icon-theme

%description
Venus Mouse configures the wired UtechSmart Venus MMO gaming mouse
(USB 04d9:fc55) on Linux: button assignments, DPI stages, polling rate,
scroll speed, lighting, macros and the five on-mouse profiles.

The package installs a udev rule that gives the logged-in desktop user
access to the mouse's configuration interface only (no root access needed),
and an optional "venus-mouse" system group for non-desktop access.

%prep
%autosetup

%build
# Nothing to build: pure Python, run from its private directory.

%install
%{python3} packaging/install_tree.py %{buildroot}

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{app_id}.desktop
# The package must stay Python-version independent: no compiled files.
test -z "$(find %{buildroot} -name '*.py[co]' -o -name __pycache__)"

%files
%license LICENSE
%license src/venus_profile/gui/data/fonts/AlexBrush-OFL.txt
%doc README.md
%{_bindir}/venus-mouse
%{_datadir}/venus-mouse/
%{_datadir}/applications/%{app_id}.desktop
%{_datadir}/icons/hicolor/*/apps/%{app_id}.png
%{_datadir}/icons/hicolor/scalable/apps/%{app_id}.svg
%{_udevrulesdir}/70-utechsmart-venus.rules
%{_sysusersdir}/venus-mouse.conf

%changelog
* Sat Nov 21 2026 Berry Dingle <dzll-launcher@tutamail.com> - 0.2.3-1
- Buttons: Shift, Ctrl, Alt, Super and Tab can be assigned as keyboard keys
- Macros: deleting a macro asks for confirmation again
- Settings: pointer speed, acceleration and double-click speed now read and
  change the desktop's mouse settings (KDE Plasma Wayland, GNOME) on Apply

* Sat Nov 21 2026 Berry Dingle <dzll-launcher@tutamail.com> - 0.2.2-1
- Macros: toggle loop mode now toggles (count FF FF, as the Windows utility sends)

* Fri Nov 20 2026 Berry Dingle <dzll-launcher@tutamail.com> - 0.2.1-1
- Macros: loop mode changes (repeat fixed, while held, toggle) now reach the mouse

* Fri Nov 20 2026 Berry Dingle <dzll-launcher@tutamail.com> - 0.2.0-1
- First rpm: one noarch package for Fedora 44, 45 and later

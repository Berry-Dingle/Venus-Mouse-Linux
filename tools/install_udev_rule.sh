#!/usr/bin/env bash
set -euo pipefail

RULE_NAME="70-utechsmart-venus.rules"
GROUP_NAME="venus-mouse"
INSTALL_PATH="/etc/udev/rules.d/${RULE_NAME}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
SOURCE_RULE="${PROJECT_DIR}/packaging/udev/${RULE_NAME}"
MODE="plan"
ALLOW_REPLACE=0

usage() {
    cat <<'EOF'
Usage:
  tools/install_udev_rule.sh
  sudo tools/install_udev_rule.sh --install [--replace]

Without --install, this script only prints the planned administrator actions.
--replace explicitly permits replacing a differing existing project rule.
EOF
}

for argument in "$@"; do
    case "${argument}" in
        --install) MODE="install" ;;
        --replace) ALLOW_REPLACE=1 ;;
        --help|-h) usage; exit 0 ;;
        *) echo "error: unknown argument: ${argument}" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ ! -f "${SOURCE_RULE}" ]]; then
    echo "error: source rule does not exist: ${SOURCE_RULE}" >&2
    exit 1
fi

cat <<EOF
Venus udev installation plan:
  source: ${SOURCE_RULE}
  target: ${INSTALL_PATH}
  group:  ${GROUP_NAME}
  mode:   0660 on matching hidraw interface 02 only
EOF

if [[ "${MODE}" != "install" ]]; then
    cat <<'EOF'
No changes made. To install explicitly:
  sudo tools/install_udev_rule.sh --install
EOF
    exit 0
fi

if [[ "${EUID}" -ne 0 ]]; then
    echo "error: --install must be run through sudo" >&2
    exit 1
fi
if [[ -z "${SUDO_USER:-}" || "${SUDO_USER}" == "root" ]]; then
    echo "error: SUDO_USER must identify the non-root invoking user" >&2
    exit 1
fi
if ! id -u -- "${SUDO_USER}" >/dev/null 2>&1; then
    echo "error: SUDO_USER is not a local account: ${SUDO_USER}" >&2
    exit 1
fi

if [[ -e "${INSTALL_PATH}" ]] && ! cmp -s -- "${SOURCE_RULE}" "${INSTALL_PATH}"; then
    if [[ "${ALLOW_REPLACE}" -ne 1 ]]; then
        cat >&2 <<EOF
error: ${INSTALL_PATH} exists and differs from the packaged rule.
Review it, then rerun with --install --replace if replacement is intended.
EOF
        exit 1
    fi
fi

if ! getent group "${GROUP_NAME}" >/dev/null; then
    groupadd --system "${GROUP_NAME}"
    echo "Created system group ${GROUP_NAME}."
else
    echo "Group ${GROUP_NAME} already exists."
fi

if id -nG -- "${SUDO_USER}" | tr ' ' '\n' | grep -Fqx -- "${GROUP_NAME}"; then
    echo "User ${SUDO_USER} is already a member of ${GROUP_NAME}."
else
    usermod -a -G "${GROUP_NAME}" "${SUDO_USER}"
    echo "Added ${SUDO_USER} to ${GROUP_NAME}."
fi

if [[ -e "${INSTALL_PATH}" ]] && cmp -s -- "${SOURCE_RULE}" "${INSTALL_PATH}"; then
    echo "Rule is already installed and identical."
else
    install -D -o root -g root -m 0644 -- "${SOURCE_RULE}" "${INSTALL_PATH}"
    echo "Installed ${INSTALL_PATH}."
fi

udevadm control --reload-rules
echo "Reloaded udev rules."
cat <<EOF
No broad udev trigger was run. Unplug and reconnect the mouse so only its new
device event is processed.

Group membership may require logout/login (or a new login session).

After reconnecting, verify without opening hidraw:
  cd "${PROJECT_DIR}"
  PYTHONPATH=src python3 -m venus_profile.verify_udev_access

Additional checks:
  id -nG -- "${SUDO_USER}"
  getent group "${GROUP_NAME}"
  stat -c '%n owner=%U group=%G mode=%a' /dev/hidraw*
EOF

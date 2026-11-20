#!/usr/bin/env bash
set -euo pipefail

RULE_NAME="70-utechsmart-venus.rules"
INSTALL_PATH="/etc/udev/rules.d/${RULE_NAME}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
SOURCE_RULE="${PROJECT_DIR}/packaging/udev/${RULE_NAME}"
MODE="plan"

usage() {
    cat <<'EOF'
Usage:
  tools/uninstall_udev_rule.sh
  sudo tools/uninstall_udev_rule.sh --uninstall

Without --uninstall, this script only prints the planned removal.
EOF
}

for argument in "$@"; do
    case "${argument}" in
        --uninstall) MODE="uninstall" ;;
        --help|-h) usage; exit 0 ;;
        *) echo "error: unknown argument: ${argument}" >&2; usage >&2; exit 2 ;;
    esac
done

echo "Project rule targeted for removal: ${INSTALL_PATH}"
if [[ "${MODE}" != "uninstall" ]]; then
    echo "No changes made. To uninstall explicitly:"
    echo "  sudo tools/uninstall_udev_rule.sh --uninstall"
    exit 0
fi
if [[ "${EUID}" -ne 0 ]]; then
    echo "error: --uninstall must be run through sudo" >&2
    exit 1
fi
if [[ ! -e "${INSTALL_PATH}" ]]; then
    echo "Rule is not installed; nothing to remove."
elif [[ ! -f "${SOURCE_RULE}" ]]; then
    echo "error: packaged source rule is missing; refusing removal" >&2
    exit 1
elif ! cmp -s -- "${SOURCE_RULE}" "${INSTALL_PATH}"; then
    echo "error: installed file differs from this project's rule; refusing removal" >&2
    exit 1
else
    rm -- "${INSTALL_PATH}"
    echo "Removed only ${INSTALL_PATH}."
fi

udevadm control --reload-rules
echo "Reloaded udev rules."
cat <<'EOF'
No group or user membership was removed, and no broad trigger was run.
Unplug/replug the mouse to recreate its hidraw node under remaining rules.
EOF

"""Static audit proving the GUI cannot reach device communication code.

The audit parses (never imports) every module reachable from the GUI entry
point, following relative and absolute ``venus_profile`` imports including
``from package import module`` forms.  It fails if the closure contains any
device-facing module or low-level I/O primitive, or if a GUI module uses
dynamic import/evaluation that static analysis could not follow.

Three narrow, deliberate exceptions (``docs/DEVICE_READ_BOUNDARY_DESIGN.md``,
``investigation/LIVE_EVENT_FINDINGS.md``, ``docs/LIVE_LIGHTING_PREVIEW_DESIGN.md``):
``gui/device_read.py`` may launch the read-only snapshot helper,
``gui/device_events.py`` the listen-only event helper and
``gui/lighting_preview.py`` the one-shot lighting helper, each from exactly one
call site with the literal argument list ``[sys.executable, "-m", HELPER_MODULE]``
and a constant ``HELPER_MODULE`` (``venus_profile.read_snapshot``,
``venus_profile.watch_events`` and ``venus_profile.preview_lighting``).
No other GUI module may launch a process by any means.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ENTRY_MODULE = "venus_profile.gui.application"

FORBIDDEN_MODULES = frozenset({
    "venus_profile.readonly_hidraw",
    "venus_profile.device_session",
    "venus_profile.read_device_memory",
    "venus_profile.visual_colour_cycle",
    "venus_profile.device_diagnostics",
    "venus_profile.verify_udev_access",
    "venus_profile.preview_lighting",
})
FORBIDDEN_PREFIXES = ("venus_profile.guarded_",)
FORBIDDEN_STDLIB = frozenset({
    "fcntl", "ctypes", "socket", "subprocess", "usb", "hid", "hidapi", "pyudev", "evdev",
})
FORBIDDEN_GUI_CALLS = frozenset({"__import__", "exec", "eval", "import_module"})
FORBIDDEN_TOKENS = ("/dev/" + "hidraw", "/dev/bus/" + "usb", "usb" + "mon", "HIDIOC")
# Process-launch APIs. Only the single reviewed helper launch is allowed.
LAUNCH_TOKENS = ("Gio." + "Subprocess", "Subprocess" + "Launcher", "GLib." + "spawn", "spawn_" + "async",
                 "spawn_command" + "_line", "os." + "system", "os." + "exec", "os." + "posix_spawn",
                 "os." + "popen", "os." + "fork")
#: GUI file -> the only helper it may launch.
HELPER_LAUNCHES = {
    "device_read.py": "venus_profile.read_snapshot",
    "device_events.py": "venus_profile.watch_events",
    "lighting_preview.py": "venus_profile.preview_lighting",
}


def _audit_helper_launch(path: Path, helper_module: str | None = None) -> None:
    """An allowed launch: a literal helper argv, one call site."""
    expected_module = helper_module or HELPER_LAUNCHES[path.name]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    helper_values = [
        node.value.value for node in tree.body
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "HELPER_MODULE"
                                                for t in node.targets)
        and isinstance(node.value, ast.Constant)
    ]
    if helper_values != [expected_module]:
        raise RuntimeError(f"helper launch must name exactly {expected_module}")
    argv_assignments = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "argv" for t in node.targets)
    ]
    expected = 'argv = [sys.executable, "-m", HELPER_MODULE]'
    if len(argv_assignments) != 1 or ast.unparse(argv_assignments[0]) != expected.replace('"', "'"):
        raise RuntimeError("helper launch argv must be exactly " + expected)
    launches = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and ast.unparse(node.func) == "Gio.Subprocess.new"
    ]
    if len(launches) != 1 or not launches[0].args or ast.unparse(launches[0].args[0]) != "argv":
        raise RuntimeError("helper must be launched by one Gio.Subprocess.new(argv, ...) call")


def _module_path(module: str) -> Path | None:
    parts = module.split(".")
    if parts[0] != "venus_profile":
        return None
    base = PACKAGE_ROOT.joinpath(*parts[1:])
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None


def _package_of(module: str, path: Path) -> str:
    return module if path.name == "__init__.py" else module.rsplit(".", 1)[0]


def _imports(module: str, path: Path) -> tuple[set[str], set[str], ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = _package_of(module, path)
    internal: set[str] = set()
    external: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("venus_profile"):
                    internal.add(alias.name)
                else:
                    external.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = parts[: len(parts) - (node.level - 1)]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            if target.startswith("venus_profile"):
                internal.add(target)
                for alias in node.names:
                    candidate = f"{target}.{alias.name}"
                    if _module_path(candidate) is not None:
                        internal.add(candidate)
            elif target:
                external.add(target.split(".")[0])
    return internal, external, tree


def import_closure(entry: str = ENTRY_MODULE) -> dict[str, set[str]]:
    """Map every reachable package module to its external imports."""
    seen: dict[str, set[str]] = {}
    pending = [entry]
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        path = _module_path(module)
        if path is None:
            continue
        internal, external, _tree = _imports(module, path)
        seen[module] = external
        parts = module.split(".")
        pending.extend(".".join(parts[:index]) for index in range(1, len(parts)))
        pending.extend(internal)
    return seen


def safety_audit() -> list[str]:
    closure = import_closure()
    for module, external in closure.items():
        if module in FORBIDDEN_MODULES or module.startswith(FORBIDDEN_PREFIXES):
            raise RuntimeError(f"device-facing module reachable from the GUI: {module}")
        blocked = external & FORBIDDEN_STDLIB
        if blocked:
            raise RuntimeError(f"low-level I/O import {sorted(blocked)} reachable from the GUI via {module}")
    gui_root = Path(__file__).parent
    for path in gui_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if path != Path(__file__) and any(token in source for token in FORBIDDEN_TOKENS):
            raise RuntimeError(f"device-facing token in GUI module: {path}")
        if path == Path(__file__):
            continue
        for token in LAUNCH_TOKENS:
            if token in source:
                if path.name in HELPER_LAUNCHES and token == "Gio." + "Subprocess":
                    continue
                raise RuntimeError(f"process launch {token!r} in GUI module: {path}")
        if path.name in HELPER_LAUNCHES:
            _audit_helper_launch(path)
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in FORBIDDEN_GUI_CALLS:
                    raise RuntimeError(f"dynamic import/evaluation in GUI module: {path}")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names]
                module = getattr(node, "module", None) or ""
                if "importlib" in module.split(".") and "import_module" in names:
                    raise RuntimeError(f"dynamic import in GUI module: {path}")
    return [
        f"GUI import closure ({len(closure)} modules) contains no device-facing module",
        "GUI import closure contains no fcntl/ctypes/socket/subprocess/USB/HID import",
        "GUI modules use no dynamic import or evaluation and no device paths",
        "GUI launches no process except the read-only snapshot helper, the listen-only "
        "event helper and the one-shot lighting helper (one literal call site each)",
    ]

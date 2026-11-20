"""Static safety audit for the read-only snapshot helper (``read_snapshot``).

Kept separate from the helper so that the audit's own imports (the GUI audit's
static import-closure walker) are not part of the helper's closure.
"""

from __future__ import annotations

from pathlib import Path

from .gui.safety import import_closure  # static AST walk; never imports what it parses


#: Modules that hold write paths or report builders; never in the helper.
FORBIDDEN_SNAPSHOT_MODULES = frozenset({
    "venus_profile.button_assignment_write",
    "venus_profile.visual_colour_cycle",
    "venus_profile.device_diagnostics",
    "venus_profile.apply_plan",
})
FORBIDDEN_SNAPSHOT_PREFIXES = ("venus_profile.guarded_", "venus_profile.gui")
FORBIDDEN_SNAPSHOT_EXTERNAL = frozenset({"socket", "ctypes", "usb", "hid", "hidapi", "pyudev", "evdev"})
#: subprocess is reachable only through the offline capture parser (tshark),
#: which the helper never calls.
SUBPROCESS_ALLOWED_IN = frozenset({"venus_profile.offline_usb_capture"})


def snapshot_safety_audit() -> list[str]:
    """Static proof that the helper can read but has no way to write."""
    closure = import_closure("venus_profile.read_snapshot")
    for required in ("venus_profile.readonly_hidraw", "venus_profile.device_session"):
        if required not in closure:
            raise RuntimeError(f"snapshot helper does not use the read path: {required} missing")
    for module, external in closure.items():
        if module in FORBIDDEN_SNAPSHOT_MODULES or module.startswith(FORBIDDEN_SNAPSHOT_PREFIXES):
            raise RuntimeError(f"write-capable or GUI module reachable from the snapshot helper: {module}")
        blocked = external & FORBIDDEN_SNAPSHOT_EXTERNAL
        if blocked:
            raise RuntimeError(f"low-level I/O import {sorted(blocked)} in snapshot helper via {module}")
        if "subprocess" in external and module not in SUBPROCESS_ALLOWED_IN:
            raise RuntimeError(f"subprocess reachable from the snapshot helper via {module}")
    source = (Path(__file__).parent / "read_snapshot.py").read_text(encoding="utf-8")
    if "argparse" in source or "sys.argv[1:] if argv is None else argv" not in source:
        raise RuntimeError("snapshot helper must not accept options")
    return [
        f"snapshot helper import closure ({len(closure)} modules) includes the read-only transport",
        "snapshot helper reaches no guarded writer, report builder, GUI or socket/ctypes/USB/HID module",
        "snapshot helper takes no options; subprocess only via the offline capture parser",
    ]


# --- Listen-only event probe (venus_profile.event_listener) -----------------

LISTENER_MODULE = "venus_profile.event_listener"
#: Names the probe may take from the read transport: discovery only.
LISTENER_ALLOWED_TRANSPORT_NAMES = frozenset({
    "VENUS_INTERFACE", "VENUS_PRODUCT_ID", "VENUS_USAGE", "VENUS_USAGE_PAGE",
    "VENUS_VENDOR_ID", "HidrawDevice", "HidrawDiscoveryError", "discover_venus_hidraw",
})
#: Anything that could send to the device, or open it for writing.
LISTENER_FORBIDDEN_NAMES = frozenset({
    "ioctl", "fcntl", "send_f2", "get_feature", "get_feature_report", "read_memory",
    "build_f2_request", "ReadOnlyHidrawTransport", "_HidrawFeatureIo", "DeviceSession",
    "O_RDWR", "O_WRONLY", "write", "writev", "pwrite", "pwritev",
})
LISTENER_FORBIDDEN_EXTERNAL = FORBIDDEN_SNAPSHOT_EXTERNAL | {"fcntl", "subprocess", "argparse"}


WATCHER_MODULE = "venus_profile.watch_events"


def _listen_only_audit(module_name: str, source_path: Path, *, allow_files: bool) -> int:
    """Shared rules for modules that may only read input reports.

    Returns the size of the module's import closure.
    """
    import ast

    closure = import_closure(module_name)
    for module in closure:
        if (module in FORBIDDEN_SNAPSHOT_MODULES or module.startswith(FORBIDDEN_SNAPSHOT_PREFIXES)
                or module == "venus_profile.device_session"):
            raise RuntimeError(f"write-capable, session or GUI module reachable from the listener: {module}")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    opens = []
    for node in ast.walk(tree):
        if not allow_files and isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "open":
            raise RuntimeError("listen-only helper must not open files")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in LISTENER_FORBIDDEN_EXTERNAL:
                    raise RuntimeError(f"listener imports {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and (node.module or "").split(".")[0] in LISTENER_FORBIDDEN_EXTERNAL:
                raise RuntimeError(f"listener imports from {node.module}")
            if node.level == 1:
                if node.module != "readonly_hidraw":
                    raise RuntimeError(f"listener imports project module {node.module}")
                extra = {alias.name for alias in node.names} - LISTENER_ALLOWED_TRANSPORT_NAMES
                if extra:
                    raise RuntimeError(f"listener takes more than discovery from the transport: {sorted(extra)}")
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os" \
                and node.attr in LISTENER_FORBIDDEN_NAMES:
            raise RuntimeError(f"listener uses os.{node.attr}")
        elif isinstance(node, ast.Name) and node.id in LISTENER_FORBIDDEN_NAMES:
            raise RuntimeError(f"listener references {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in LISTENER_FORBIDDEN_NAMES - {"write"}:
            raise RuntimeError(f"listener references .{node.attr}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "opener":
            opens.append(node)
    if len(opens) != 1:
        raise RuntimeError(f"listener must open the device exactly once, found {len(opens)}")
    flags = ast.dump(opens[0].args[1]) if len(opens[0].args) == 2 else ""
    if "attr='O_RDONLY'" not in flags:
        raise RuntimeError("listener must open the device with O_RDONLY")
    source = source_path.read_text(encoding="utf-8")
    if "sys.argv[1:] if argv is None else argv" not in source:
        raise RuntimeError("listener must not accept options")
    return len(closure)


def event_listener_safety_audit(path: Path | None = None) -> list[str]:
    """Static proof that the event probe only reads input reports."""
    size = _listen_only_audit(LISTENER_MODULE, path or Path(__file__).parent / "event_listener.py",
                              allow_files=True)
    return [
        f"event listener import closure ({size} modules) has no writer, session or GUI module",
        "event listener takes only discovery from the transport; no ioctl, feature report or os.write",
        "event listener opens the device once, read-only, and takes no options",
    ]


def watch_events_safety_audit(path: Path | None = None) -> list[str]:
    """Static proof that the GUI's event helper only reads input reports."""
    size = _listen_only_audit(WATCHER_MODULE, path or Path(__file__).parent / "watch_events.py",
                              allow_files=False)
    return [
        f"event helper import closure ({size} modules) has no writer, session or GUI module",
        "event helper takes only discovery from the transport; no ioctl, feature report, os.write or file",
        "event helper opens the device once, read-only, and takes no options",
    ]


# --- One-shot live lighting writer (venus_profile.preview_lighting) ---------

PREVIEW_MODULE = "venus_profile.preview_lighting"
PREVIEW_PLAN_MODULE = "venus_profile.lighting_preview_plan"
#: Command bytes that must never appear as literals in the plan module, and
#: F5 / other report IDs that must never appear in the helper's templates.
_F_COMMANDS = {0xF1, 0xF3, 0xF5}


def preview_lighting_safety_audit(path: Path | None = None, plan_path: Path | None = None) -> list[str]:
    """Static proof that the lighting helper can only send the fixed lighting templates."""
    import ast

    from . import lighting_preview_plan as plan

    closure = import_closure(PREVIEW_MODULE)
    for required in ("venus_profile.readonly_hidraw", "venus_profile.device_session", PREVIEW_PLAN_MODULE):
        if required not in closure:
            raise RuntimeError(f"lighting helper does not use {required}")
    for module, external in closure.items():
        if module in FORBIDDEN_SNAPSHOT_MODULES or module.startswith(FORBIDDEN_SNAPSHOT_PREFIXES):
            raise RuntimeError(f"guarded-writer or GUI module reachable from the lighting helper: {module}")
        blocked = external & FORBIDDEN_SNAPSHOT_EXTERNAL
        if blocked:
            raise RuntimeError(f"low-level I/O import {sorted(blocked)} in lighting helper via {module}")
        if "subprocess" in external and module not in SUBPROCESS_ALLOWED_IN:
            raise RuntimeError(f"subprocess reachable from the lighting helper via {module}")

    # Fixed field table.
    if plan.RECORD_ADDRESSES != (0x0449, 0x0451, 0x0459, 0x0461, 0x0469) or plan.RECORD_LENGTH != 6 \
            or plan.BRIGHTNESS_ADDRESSES != (0x044F, 0x0457, 0x045F, 0x0467, 0x046F) \
            or plan.ACTIVE_PROFILE_ADDRESS != 0x002C or plan.PROFILE_COUNT != 5 \
            or plan.POLLING_WRITE_ADDRESSES != (0x0032, 0x0038) or plan.POLLING_ENUMS != (8, 4, 2, 1) \
            or plan.POLLING_READS != ((0x0032, 5), (0x0038, 3)) \
            or plan.DPI_PROFILE_BASES != (0x0040, 0x0100, 0x01B0, 0x0260, 0x0310) \
            or (plan.DPI_READ_OFFSET, plan.DPI_READ_LENGTH, plan.DPI_RECORD_LENGTH) != (2, 32, 5) \
            or (plan.DPI_STAGE_SELECT_OFFSET, plan.DPI_STAGE_SELECT_LENGTH, plan.DPI_STAGE_COUNT) != (2, 2, 5) \
            or tuple(plan.dpi_stage_select_address(p) for p in range(1, 6)) != (0x42, 0x102, 0x1B2, 0x262, 0x312) \
            or plan.BUTTON_PROFILE_BASES != (0x0082, 0x0142, 0x01F2, 0x02A2, 0x0352) \
            or plan.BUTTON_OFFSETS != tuple(range(0, 72, 4)) + (0x58, 0x5C) or plan.BUTTON_RECORD_LENGTH != 4 \
            or (plan.MACRO_SLOT_BASE, plan.MACRO_SLOT_STRIDE, plan.MACRO_BODY_LENGTH, plan.MACRO_SLOT_COUNT,
                plan.MACRO_REPORT_LENGTH) != (0x0478, 0xC8, 200, 15, 256) \
            or plan.macro_slot_address(14) + plan.MACRO_BODY_LENGTH != 0x1030 \
            or (plan.SCROLL_ADDRESS, plan.SCROLL_LENGTH, plan.SCROLL_REPORT_LENGTH, plan.SCROLL_MIN,
                plan.SCROLL_MAX) != (0x0020, 10, 64, 1, 63):
        raise RuntimeError("lighting/profile field table differs from the evidenced addresses")

    plan_source = (plan_path or Path(plan.__file__)).read_text(encoding="utf-8")
    plan_tree = ast.parse(plan_source)
    plan_ints = {node.value for node in ast.walk(plan_tree)
                 if isinstance(node, ast.Constant) and isinstance(node.value, int)
                 and not isinstance(node.value, bool)}
    if plan_ints & _F_COMMANDS:
        raise RuntimeError("command literal found in the pure lighting plan module")
    for node in ast.walk(plan_tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names]
            module = getattr(node, "module", None) or ""
            if node.__class__ is ast.ImportFrom and node.level:
                raise RuntimeError(f"lighting plan module imports project module {module}")
            if any(name.split(".")[0] not in {"__future__", "dataclasses", "json"} for name in
                   ([module] if module else names)):
                raise RuntimeError(f"lighting plan module imports {module or names}")

    source_path = path or Path(__file__).parent / "preview_lighting.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    if "argparse" in source or "sys.argv[1:] if argv is None else argv" not in source:
        raise RuntimeError("lighting helper must not accept options")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            raise RuntimeError("lighting helper must not open files")
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and node.value == 0xF5:
            raise RuntimeError("lighting helper contains an F5 literal")
    hex_templates = {node.value.replace(" ", "").lower() for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "fromhex"
                     for node in node.args if isinstance(node, ast.Constant)}
    if hex_templates != ({"02f1" + sub + "00" * 12 for sub in ("0204", "0201", "0202", "0208", "0210")}
                         | {"02f5" + sub + "00" * 13 for sub in ("00", "01")} | {"02f3"}):
        raise RuntimeError(f"lighting helper report templates changed: {sorted(hex_templates)}")

    # Exactly one ioctl, inside a method that validates first.
    ioctl_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and getattr(node.func, "attr", None) == "_ioctl"]
    if len(ioctl_calls) != 1:
        raise RuntimeError(f"lighting helper must have exactly one ioctl call, found {len(ioctl_calls)}")
    submitters = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                  and any(child is ioctl_calls[0] for child in ast.walk(node))]
    first = submitters[0].body[0] if submitters and submitters[0].body else None
    if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
            and getattr(first.value.func, "id", None) == "validate_preview_report"):
        raise RuntimeError("the ioctl method must validate the report as its first statement")

    # The write fd is opened only after the pre-read guard, in one place.
    transaction = next((node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "write_lighting"), None)
    if transaction is None:
        raise RuntimeError("lighting helper has no write_lighting transaction")
    text = ast.unparse(transaction)
    order = [text.find(token) for token in ("_read_lighting(session)", "lighting_is_known(",
                                            "plan_reports(", "backend_factory(", "LIGHTING_FINALISATION_REPORT",
                                            "read_back = _read_lighting(session)")]
    if -1 in order or order != sorted(order):
        raise RuntimeError("write_lighting must pre-read, guard, plan, open the writer, finalise, then read back")
    profile_tx = next((node for node in tree.body if isinstance(node, ast.FunctionDef)
                       and node.name == "write_active_profile"), None)
    if profile_tx is None:
        raise RuntimeError("lighting helper has no write_active_profile transaction")
    text = ast.unparse(profile_tx)
    order = [text.find(token) for token in ("_stable_read(session, ACTIVE_PROFILE_ADDRESS, 1)",
                                            "current >= PROFILE_COUNT", "build_profile_report(",
                                            "backend_factory(", "PROFILE_SWITCH_SEQUENCE",
                                            "read_back = _stable_read(session, ACTIVE_PROFILE_ADDRESS, 1)")]
    if -1 in order or order != sorted(order):
        raise RuntimeError("write_active_profile must pre-read, guard, build, open the writer, finalise, "
                           "then read back")
    polling_tx = next((node for node in tree.body if isinstance(node, ast.FunctionDef)
                       and node.name == "write_polling"), None)
    if polling_tx is None:
        raise RuntimeError("lighting helper has no write_polling transaction")
    polling_text = ast.unparse(polling_tx)
    order = [polling_text.find(token) for token in ("before = _read_polling(session)", "if current is None",
                                                    "build_polling_reports(", "backend_factory(",
                                                    "PROFILE_APPLY_REPORT_08",
                                                    "read_back = polling_from_reads(*_read_polling(session))")]
    if -1 in order or order != sorted(order):
        raise RuntimeError("write_polling must pre-read, guard, build, open the writer, finalise, then read back")
    dpi_tx = next((node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == "write_dpi"), None)
    if dpi_tx is None:
        raise RuntimeError("lighting helper has no write_dpi transaction")
    dpi_text = ast.unparse(dpi_tx)
    order = [dpi_text.find(token) for token in ("before = {profile: _read_dpi_block(session, profile)",
                                                "is None:", "build_dpi_report(", "backend_factory(",
                                                "DPI_SEQUENCE",
                                                "read_back = {profile: _read_dpi_block(session, profile)")]
    if -1 in order or order != sorted(order):
        raise RuntimeError("write_dpi must pre-read, guard, build, open the writer, finalise, then read back")
    stage_tx = next((node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == "write_dpi_stage"), None)
    if stage_tx is None:
        raise RuntimeError("lighting helper has no write_dpi_stage transaction")
    stage_text = ast.unparse(stage_tx)
    order = [stage_text.find(token) for token in ("before = {profile: _stable_read(session, dpi_stage_select_address(",
                                                  "if current[profile] is None:", "build_dpi_stage_report(",
                                                  "backend_factory(", "DPI_STAGE_SEQUENCE",
                                                  "read_back = {profile: _stable_read(session, "
                                                  "dpi_stage_select_address(")]
    if -1 in order or order != sorted(order) or stage_text.count("backend_factory(") != 1:
        raise RuntimeError("write_dpi_stage must pre-read, guard, build, open the writer, finalise, then read back")
    buttons_tx = next((node for node in tree.body if isinstance(node, ast.FunctionDef)
                       and node.name == "write_buttons"), None)
    if buttons_tx is None:
        raise RuntimeError("lighting helper has no write_buttons transaction")
    buttons_text = ast.unparse(buttons_tx)
    order = [buttons_text.find(token) for token in ("before = {key: _stable_read(", "if not button_record_is_known(",
                                                    "build_button_report(", "backend_factory(",
                                                    "read_back = {key: _stable_read(")]
    if -1 in order or order != sorted(order) or "_REPORT" in buttons_text.replace("build_button_report", ""):
        raise RuntimeError("write_buttons must pre-read, guard, build, open the writer, read back; no finaliser")
    macros_tx = next((node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == "write_macros"), None)
    if macros_tx is None:
        raise RuntimeError("lighting helper has no write_macros transaction")
    macros_text = ast.unparse(macros_tx)
    order = [macros_text.find(token) for token in ("before = {slot: _read_macro_slot(", "if not macro_body_is_known(",
                                                   "build_macro_report(", "backend_factory(",
                                                   "writer.submit(MACRO_BEGIN_REPORT)", "writer.submit(report)",
                                                   "writer.submit(MACRO_END_REPORT)",
                                                   "read_back = {slot: _read_macro_slot(")]
    if -1 in order or order != sorted(order):
        raise RuntimeError("write_macros must pre-read, guard, build, open the writer, bracket each upload "
                           "with F5 00 / F5 01, then read back")
    if source.count("MACRO_BEGIN_REPORT)") != 1 or source.count("MACRO_END_REPORT)") != 1:
        raise RuntimeError("F5 00 / F5 01 may only be sent inside write_macros")
    if macros_text.count("backend_factory(") != 1:
        raise RuntimeError("the write-capable fd must be opened once in each transaction and nowhere else")
    scroll_tx = next((node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == "write_scroll"), None)
    if scroll_tx is None:
        raise RuntimeError("lighting helper has no write_scroll transaction")
    scroll_text = ast.unparse(scroll_tx)
    order = [scroll_text.find(token) for token in ("before = _stable_read(session, SCROLL_ADDRESS",
                                                   "if current is None", "build_scroll_report(",
                                                   "backend_factory(", "SCROLL_SEQUENCE",
                                                   "read_back = scroll_from_read(")]
    if -1 in order or order != sorted(order) or scroll_text.count("backend_factory(") != 1:
        raise RuntimeError("write_scroll must pre-read, guard, build, open the writer, finalise, then read back")
    if source.count("backend_factory(") != 8 or "backend_factory(" not in ast.unparse(transaction) \
            or text.count("backend_factory(") != 1 or polling_text.count("backend_factory(") != 1 \
            or dpi_text.count("backend_factory(") != 1 or buttons_text.count("backend_factory(") != 1:
        raise RuntimeError("the write-capable fd must be opened once in each transaction and nowhere else")
    reader = next((node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == "_read_lighting"), None)
    if reader is None or "_stable_read(session, address)" not in ast.unparse(reader) \
            or "read_memory" in ast.unparse(reader):
        raise RuntimeError("every lighting read must go through _stable_read (two agreeing replies)")
    stable = next((node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == "_stable_read"), None)
    if stable is None or "replies[-1] == replies[-2]" not in ast.unparse(stable):
        raise RuntimeError("_stable_read must require two consecutive agreeing replies")
    return [
        f"lighting helper import closure ({len(closure)} modules) has no guarded writer, GUI or "
        "socket/ctypes/USB/HID module",
        "lighting helper sends only F3 to the 10 fixed lighting fields, 0x002C (profile 0-4) or polling "
        "0x0032/6 + 0x0038/4 (8/4/2/1, companions 00), the 25 DPI stage records (enabled, evidenced "
        "lookup/selector), the 5 DPI start-up stage bytes (base+2 = 0-4, 00) or the 100 button records (decoder-recognised shapes), and the exact "
        "F1 02 04 / 02 01 / 02 02 / 02 08 / 02 10; F5 00 / F5 01 only around a Report-4 macro upload "
        "to one of the 15 slots (known action list); Report 3 only for scroll lines at 0x0020 (1-63); "
        "no other report ID",
        "every write passes validate_preview_report immediately before the single ioctl",
        "write fd opened once, after the pre-read guard; read-back follows finalisation",
        "every lighting read (pre-read and read-back) needs two consecutive agreeing replies",
        "lighting helper takes no options and opens no files; plan module is pure",
    ]


# --- Read-only probe of unread bytes (venus_profile.read_probe, user-run) ----

def read_probe_safety_audit(path: Path | None = None) -> list[str]:
    """Static proof that the probe only reads its fixed requests."""
    import ast

    closure = import_closure("venus_profile.read_probe")
    for module, external in closure.items():
        if module in FORBIDDEN_SNAPSHOT_MODULES or module.startswith(FORBIDDEN_SNAPSHOT_PREFIXES) \
                or module in ("venus_profile.preview_lighting", "venus_profile.lighting_preview_plan"):
            raise RuntimeError(f"write-capable or GUI module reachable from the probe: {module}")
        blocked = external & FORBIDDEN_SNAPSHOT_EXTERNAL
        if blocked:
            raise RuntimeError(f"low-level I/O import {sorted(blocked)} in the probe via {module}")
        if "subprocess" in external and module not in SUBPROCESS_ALLOWED_IN:
            raise RuntimeError(f"subprocess reachable from the probe via {module}")
    source_path = path or Path(__file__).parent / "read_probe.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    ints = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
            and isinstance(node.value, int) and not isinstance(node.value, bool)}
    if ints & {0xF1, 0xF3, 0xF5}:
        raise RuntimeError("command literal found in the probe")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"_request_internal_capability", "submit",
                                                             "_ioctl", "send_f2", "get_feature"}:
            raise RuntimeError(f"probe references .{node.attr}")
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "add_argument":
            flags = [arg.value for arg in node.args if isinstance(arg, ast.Constant)]
            if flags != ["--live-read"] or any(
                    kw.arg in ("type", "nargs") for kw in node.keywords):
                raise RuntimeError(f"probe accepts an unexpected option {flags}")
    probe = next((node for node in tree.body if isinstance(node, ast.AnnAssign)
                  and getattr(node.target, "id", None) == "PROBE_READS"), None)
    if probe is None:
        raise RuntimeError("probe has no fixed PROBE_READS table")
    table = ast.literal_eval(probe.value)
    if [(a, n) for a, n, _why in table] != [(0x0020, 10), (0x002C, 2), (0x003E, 2), (0x0446, 4)]:
        raise RuntimeError("probe read table changed")
    reads = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and getattr(node.func, "attr", None) == "read_memory"]
    if len(reads) != 1:
        raise RuntimeError("probe must read in exactly one place")
    return [
        f"probe import closure ({len(closure)} modules) has no writer, lighting helper or GUI module",
        "probe has no F1/F3/F5 literal, capability request, submit or raw feature call",
        "probe reads only its fixed table (0x0020/10, 0x002C/2, 0x003E/2, 0x0446/4) via read_memory",
        "probe's only option is --live-read (no address, length or file input)",
    ]

# Developer guide

Technical notes for working on Venus Mouse: the command-line tools, the test
suite, and the per-setting APIs with the evidence behind them. Users don't
need any of this; see the [README](../README.md).

Other references in this folder:

- `GUI_ARCHITECTURE.md`: application structure, persistence, Save semantics,
  safety audit
- `WINDOWS_WORKFLOW.md`: how the Windows utility behaves, and the Linux
  equivalent
- `FORMAT_SPEC.md`, `BUTTON_ASSIGNMENTS.md`: file and record formats
- `APPLY_HARDWARE_TESTS.md`: what the mouse is sent for each setting, and the
  hardware tests behind it
- `DEVICE_READ_BOUNDARY_DESIGN.md`, `LIVE_LIGHTING_PREVIEW_DESIGN.md`: the
  read and write helpers and their safety limits
- `UDEV_PERMISSIONS.md`: the udev rule
- `GUARDED_*.md`: the guarded command-line write tools
- Packaging: `../packaging/appimage/`, `../packaging/rpm/`,
  `../packaging/deb/`, `../packaging/arch/` (each with a README)

Diagnostics: `VENUS_PROFILE_DIAGNOSTICS=1` makes the app print one
`venus-mouse:` line per write to the mouse on the terminal.

## Command-line tools

```bash
export PYTHONPATH=src
python -m venus_profile inspect FILE
python -m venus_profile validate FILE
python -m venus_profile decode-macro FILE
```

Separate guarded modules (`guarded_brightness`, `guarded_led_mode`,
`guarded_led_colour`, `guarded_custom_colour`, `guarded_lighting_cycle`,
`guarded_button_assignment`, `read_device_memory`, `visual_colour_cycle`)
perform narrowly scoped, user-run live operations. Each requires exact
identity checks, explicit flags, typed confirmation and immediate read-back;
their dry-run and audit modes are fully offline. Immediate read-back is not
a persistence claim. See the corresponding `docs/GUARDED_*.md` files.

## Tests

```bash
env -u WAYLAND_DISPLAY xvfb-run -a -s "-screen 0 1600x1200x24" python3 -m pytest -q
```

GUI tests need a display; under `xvfb-run` none are skipped. Tests use a
temporary application store and never touch the user's data.

The models preserve unchanged input bytes exactly. Do not overwrite official
samples.

## Delay terminology and APIs

The suite intentionally uses two distinct labels:

- **Default inserted delay** — the `DefDelayTime` editor setting, practically
  limited by the Windows UI to 0–999 ms. Use
  `MacroFile.set_default_inserted_delay_ms(value)`.
- **Action delay** — a three-byte `06 <low> <high>` record in the macro action
  list, quantized to 10 ms with a maximum of 655,350 ms. Use
  `ActionRecord.action_delay_ms(value)`.

Changing the default inserted delay does not create, remove, or alter Action
delay records. Creating an Action delay does not change `DefDelayTime`. The
older `ActionRecord.delay()` and `MacroFile.default_delay_ms` names remain
compatibility aliases, but new code and future UI text should use the
unambiguous names above.

The Windows software uses ambiguous wording around these concepts; the Linux
suite should not repeat it.

## Polling-rate API

The PFD file core supports the complete official polling enumeration:

```python
from venus_profile import PfdProfile, PollingRateHz

profile = PfdProfile.from_path("profile.pfd")
known_rate = profile.polling_rate_hz       # PollingRateHz or None
stored_enum = profile.polling_rate_raw     # original integer
profile.set_polling_rate_hz(PollingRateHz.HZ_1000)
```

Supported rates are 125, 250, 500, and 1000 Hz. Unknown stored enum values are
preserved and reported as unknown; they are not silently mapped or normalized.
Setting polling rate modifies only the semantic `PollingRate` field.

## Brightness API

```python
from venus_profile import BrightnessLevel, PfdProfile

profile = PfdProfile.from_path("profile.pfd")
known_level = profile.brightness       # BrightnessLevel or None
stored_enum = profile.brightness_raw   # original integer
profile.set_brightness(BrightnessLevel.HIGH)
```

The stored `LedState1` values are 0=Off, 1=Low, 2=Medium, and 3=High.
Unknown integers are preserved and reported as unknown. The setter rejects
unsupported levels and changes only `LedState1`; it does not copy unrelated
Windows-editor resave mutations.

## LED palette API

`LedColor1` is a decimal Windows `COLORREF` (`0x00BBGGRR`). The complete
33-entry official grid was recovered statically and is exposed as
`PaletteColor`:

```python
from venus_profile import PaletteColor, PfdProfile

profile = PfdProfile.from_path("profile.pfd")
raw = profile.led_color_raw
rgb = profile.led_color_rgb
known = profile.palette_color       # PaletteColor or None
profile.set_palette_color(PaletteColor.BLUE)
```

`RED`, `GREEN`, and `BLUE` name the three controlled grid entries. Other
entries retain stable `PALETTE_nn` names. Non-grid raw colours remain
lossless; the palette setter rejects them and changes only `LedColor1`.

Arbitrary semantic RGB is also supported:

```python
rgb = profile.led_color_rgb
hex_rgb = profile.led_color_hex
profile.set_led_color_rgb(18, 52, 86)
profile.set_led_color_hex("#123456")  # or "123456"
```

These setters correctly encode Windows COLORREF `0x00BBGGRR`. The official
custom picker has a documented quirk: typing `#123456` produced stored
`0x00123456`, which decodes as RGB `#563412`; `led_color_raw_hex` exposes
the exact stored digits for diagnostics.

A future GTK UI can use the standard colour chooser plus an exact `#RRGGBB`
entry and the official 33-colour palette. Hide alpha or force it fully opaque,
because `LedColor1` has three colour channels and an unused zero byte.

## Breathing-speed API

```python
from venus_profile import BreathingSpeed, PfdProfile

profile = PfdProfile.from_path("profile.pfd")
known_speed = profile.breathing_speed       # BreathingSpeed or None
stored_enum = profile.breathing_speed_raw   # original integer
profile.set_breathing_speed(BreathingSpeed.HIGH)
```

The mapping is 8=Low, 5=Medium, 2=High, and 0=Full Brightness. Unknown stored
integers remain lossless and are not coerced. The setter rejects unsupported
values and changes only `BreathState1`.

## LED-mode API

```python
from venus_profile import LedMode, PfdProfile

profile = PfdProfile.from_path("profile.pfd")
known_mode = profile.led_mode       # LedMode or None
stored_enum = profile.led_mode_raw  # original integer
profile.set_led_mode(LedMode.RAINBOW)
```

The mapping is 0=Breathing, 1=Full Brightness, 2=Rainbow, 3=Wave, 4=Flash,
and 5=Off. `LedMode1` alone represents the six mutually exclusive choices;
`FlashState1` remains 5 in every controlled sample and is exposed separately
as `flash_state_raw`. Unknown stored integers remain lossless, unsupported
modes are rejected, and the setter changes only `LedMode1`.
`LedMode.FULL_BRIGHTNESS` is independent from `BrightnessLevel.HIGH` and
`BreathingSpeed.FULL_BRIGHTNESS`.

## Selected DPI-stage API

```python
from venus_profile import DpiStage, PfdProfile

profile = PfdProfile.from_path("profile.pfd")
raw_index = profile.selected_dpi_stage_raw  # stored 0..4
stage = profile.selected_dpi_stage          # DpiStage 1..5 or None
profile.set_selected_dpi_stage(DpiStage.STAGE_4)
```

The GROUP `DPI` field is a zero-based selected-stage index. The public stage
is one-based. Unknown raw values remain lossless, invalid requests are
rejected, and changing selection does not alter DPI values, enable flags, or
the opaque editor-generated DPI tail.

## DPI-stage value API

```python
from venus_profile import DpiStage, PfdProfile

profile = PfdProfile.from_path("profile.pfd")
raw_x, raw_y = profile.dpi_stage_value_raw(DpiStage.STAGE_1)
dpi = profile.dpi_stage_dpi(DpiStage.STAGE_1)  # int or None
profile.set_dpi_stage_dpi(DpiStage.STAGE_1, 1600)
```

The confirmed editor domain is 200–6200 DPI in 100-DPI increments,
6400–12400 in 200-DPI increments, 12600–16200 in 300-DPI increments, plus
16400. The setter changes only that stage's packed X/Y values and structure
checksum. It leaves enable flags, selected stage, other stages, and the opaque
DPI tail untouched. Unknown or asymmetric raw X/Y values round-trip
losslessly and decode to no single semantic DPI value.

## DPI-stage enable API

```python
raw = profile.dpi_stage_enabled_raw(DpiStage.STAGE_3)  # uint32
enabled = profile.dpi_stage_enabled(DpiStage.STAGE_3)  # bool or None
profile.set_dpi_stage_enabled(DpiStage.STAGE_3, False)
```

This setter changes only the packed enable dword and checksum. It does not
change DPI values or selected stage. The Windows UI's observed fallback to
the first enabled stage was not serialized in the controlled files, so the
file-level API does not reproduce it. Unknown enable values and unusual
selected-disabled/all-disabled states remain lossless with warnings.

## Acceleration API

```python
raw = profile.acceleration_raw   # preserved decimal integer
level = profile.acceleration     # 1..4 or None
profile.set_acceleration(3)
```

The official editor stores a discrete integer level from 1 through 4.
The setter changes only GROUP `Acceleration`; pointer speed, scrolling,
double-click speed, DPI, and all packed structures remain untouched. Unknown
raw integers round-trip losslessly and are reported with a warning.

For a future UI, use four selectable steps with keyboard step 1 and page step
1. Showing the numeric level—or optionally 0%, 33%, 67%, and 100% as UI-only
guidance—is clearer than copying the Windows slider's unlabeled appearance.

## Pointer-speed API

```python
raw = profile.pointer_speed_raw  # preserved decimal integer
level = profile.pointer_speed    # 1..20 or None
profile.set_pointer_speed(10)
```

Pointer speed is GROUP `MouseSensitivity`, with levels 1 through 20.
`MouseSpeedX/Y` are separate unchanged fields. The setter modifies only
`MouseSensitivity`; unknown raw values remain lossless and generate a
warning.

For a future UI, snap to every integer, show the numeric 1–20 level, use
keyboard increment 1 and page increment 5, and optionally draw coarser marks
at 1, 5, 10, 15, and 20 without restricting intermediate values. A percentage
may be secondary guidance but should not replace the exact stored level.

## Scroll-speed API

```python
raw = profile.scroll_speed_raw  # preserved decimal integer
lines = profile.scroll_speed    # 1..63 or None
profile.set_scroll_speed(3)
```

Scroll speed is GROUP `WheelScrollLines`, with integer levels 1 through 63.
The setter changes only that field; unknown values remain lossless and
generate a warning.

For a future UI, snap to every integer, display the numeric line count, use
keyboard increment 1 and page increment 5, and show optional coarser marks
without preventing selection of intermediate values.

## Double-click-speed API

```python
level = profile.double_click_speed       # 1..10 or None
raw = profile.double_click_speed_raw     # preserved integer
milliseconds = profile.double_click_time_ms
profile.set_double_click_speed(5)
```

The stored level maps to `900 - level × 70` milliseconds, so level 1 is
830 ms and level 10 is 200 ms. Higher means faster. Unknown levels remain
lossless and generate a warning.

For a future UI, label the direction Slow→Fast, snap to all ten levels, show
both level and milliseconds, use keyboard step 1 and page step 2, and permit
coarser marks at levels 1, 5, and 10 without restricting other values.

## Offline USB-capture analysis

`venus_profile.offline_usb_capture` parses saved Wireshark JSON (when raw
packet bytes are included) or an existing pcap/pcapng via `tshark`. It only
reads capture files: it has no HID imports, device enumeration, hidraw access,
or report-write path.

To regenerate the factory-capture artefacts:

```bash
python tools/analyze_factory_capture.py
```

To regenerate the controlled brightness-write artefacts:

```bash
python tools/analyze_brightness_write_capture.py
```

To regenerate the controlled macro-write artefacts:

```bash
python tools/analyze_macro_write_capture.py
```

## Read-only hidraw milestone

The first live transport milestone is F2-only and defaults to offline fixture
validation:

```bash
PYTHONPATH=src python3 -m venus_profile.read_device_memory
```

Live discovery and the fixed 193-read plan require `--live-read` plus typed
`READ` confirmation (or the explicit `--yes-read-only` automation flag).
No F1, F3, F5, report-4/report-5, Apply, selection, preview, macro-upload, or
generic feature-report write API is present.

See [docs/READ_ONLY_HID_TRANSPORT.md](docs/READ_ONLY_HID_TRANSPORT.md).

## Guarded brightness-write milestone

The first write milestone is a separate semantic-only transport restricted to
Profile 1 address `0x044F`, values Medium (`02`) and High (`03`). It has no
generic report or memory-write API. Offline dry-run:

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_brightness --dry-run
```

See [docs/GUARDED_BRIGHTNESS_WRITE.md](docs/GUARDED_BRIGHTNESS_WRITE.md).

Confirmed native Linux changes are tracked as a validated history/overlay in
`../usb-captures/device-state/known_device_state.json`. Live memory comparison
applies the immutable Windows-captured baseline first, then explicit confirmed
Linux transitions. Malformed, conflicting, or wrong-device overlays fail
closed rather than being ignored.

## Optional udev permissions

A narrowly scoped rule and explicit administrator helpers are provided for
granting the `venus-mouse` group access to interface 02 only. Preview:

```bash
tools/install_udev_rule.sh
```

See [docs/UDEV_PERMISSIONS.md](docs/UDEV_PERMISSIONS.md). No installer helper
is invoked automatically by the Python package.

## Shared device session and lighting state

`DeviceSession` now centralises validated interface identity, metadata/access
reporting, lazy F2 transport lifecycle, complete known-plan reads, timestamped
directories, and ownership warnings. It exposes no raw report or write API.

Offline lighting inspection:

```bash
PYTHONPATH=src python3 -m venus_profile.inspect_lighting_state
```

Metadata-only session diagnostics:

```bash
PYTHONPATH=src python3 -m venus_profile.device_diagnostics
```

See [docs/DEVICE_SESSION.md](docs/DEVICE_SESSION.md) for privilege separation
and the current lighting support matrix. Offline controlled-capture evidence
now maps Profile 1 LED mode reads to `0x044C` for Breathing (`02`) and Full
Brightness (`01`). The first isolated guarded mode experiment is documented in
[docs/GUARDED_LED_MODE_WRITE.md](docs/GUARDED_LED_MODE_WRITE.md). It permits
only the exact captured six-byte transitions and remains separate from
brightness; Full Brightness ↔ Breathing is proven persistent. True Off → Full
Brightness is implemented as a separate guarded recovery experiment, while
Full Brightness → Off remains disabled.

Offline colour analysis maps Profile 1 device colour to direct RGB bytes at
`0x0449–0x044B` for the controlled official red and blue entries. Colour
writes remain limited to the isolated, fixed-prefix Blue/Red guard
documented in
[docs/GUARDED_LED_COLOUR_WRITE.md](docs/GUARDED_LED_COLOUR_WRITE.md).
Blue → Red is proven persistent and the exact reverse guard is available;
arbitrary RGB remains disabled.

The controlled official Green sample decodes as direct device RGB
`00 FF 01`. Guarded exact Green ↔ Blue operations are available; transient
`02 B5 34` and arbitrary Green/RGB writes remain disabled.

The finite 20-colour visual verification tool is documented in
[docs/VISUAL_COLOUR_CYCLE.md](docs/VISUAL_COLOUR_CYCLE.md). It is explicitly
user-paced, requires Enter before each write and after each visual result, and
records one diagnostic event rather than twenty state transitions. Its fixed
plan uses direct native `RR GG BB` bytes and has no timer, retry, restoration,
or caller-supplied colour list.

Offline custom-picker analysis confirms that Windows entered `#7B2DD2` but
wrote `D2 2D 7B` with the suffix unchanged. This is a Windows picker channel
reversal; the mouse interprets the written bytes directly as RGB.

Windows custom-picker samples show Windows reversed entered hex channels
before writing. A subsequent native experiment wrote `00 00 FF`; read-back
and power-cycle persistence succeeded, and the mouse visibly turned blue.
Device storage is therefore direct RGB. The corrected isolated recovery guard
implements only visible `#0000FF` → `#FF0000`; other live arbitrary targets
remain rejected.

The default output directory is `../usb-captures/analysis/`. Wireshark's
ordinary JSON export can omit control-IN response data; use the original
pcapng or JSON exported with packet bytes (`tshark -x`) for lossless decoding.

See [docs/FORMAT_SPEC.md](docs/FORMAT_SPEC.md) and
[docs/TEST_RESULTS.md](docs/TEST_RESULTS.md).

Rainbow read support: the controlled Full Brightness → Rainbow capture
confirms `0x044C–0x044E = 02 00 03`, with RGB and brightness preserved. This
is distinct from Breathing (`02 05 01`). Rainbow is decoded read-only; a
Rainbow live writer is not enabled. A narrowly guarded Rainbow → Full
Brightness recovery command is now implemented and remains disabled unless
the user explicitly supplies the exact source-state confirmation.

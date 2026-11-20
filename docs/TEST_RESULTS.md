# Test results

## Unified Buttons controls screen

The Buttons page now presents one unified offline screen: Top artwork and six
callouts remain visible, while the twelve Side controls are represented by a
compact 4×3 grid in the lower-left area. The separate Top/Side selector, Side
artwork runtime view, and `--demo-view` mode were removed. The header now
contains only the identity and one Demo data badge; navigation occupies the
former secondary strip. The bottom control dropdown is replaced by five
offline profile pills (`Profile: 1 2 3 4 5`), with Profile 1 selected by
default. A real X11 screenshot was captured and inspected at
`gui-review/profile-nav-shell.png`.

Focused shell, unified widget/model, and safety tests: 25 passed; display-backed
widget tests were skipped in the non-display test process. The complete suite
is 929 passed and 12 skipped; all skipped tests
are display-dependent in the non-display test process. The full suite remains
fixture-backed and offline-only, and the non-isolated wheel build succeeds.

## Offline GTK GUI foundation

Focused GUI navigation, model, hotspot, asset, and safety tests: 17 passed.
Three widget-construction/warning tests were skipped because this environment
had no usable GTK display. No application was launched and no visual rendering
claim is made.

The GUI safety audit passed. Relevant backend regressions plus GUI tests: 199
passed and 3 widget tests skipped because no GTK display was available. The
complete suite: 907 passed and 3 skipped in 32.21 seconds. All ten existing
backend safety audits and the GUI safety audit passed.

## Fixed desktop Buttons layout

The GUI now uses a non-maximised, non-resizable 1100x780 desktop window scaled
from the approved 1448x1086 (4:3) mockups. The Buttons page no longer contains
an `Adw.BreakpointBin`; Top and Side artwork use separate sizing policies, and
selection updates the summary, artwork, assignment panel, and bottom selector
through one guarded path. The shell and card CSS were rebuilt around the
measured mockup margins, header, summary strip, 60:40 card split, nested
assignment sections, and icon navigation.

Top artwork is centered in a 320x520px maximum rectangle; Side artwork is
centered in a smaller 336x233px maximum rectangle. The Top/Side selector is a
compact overlay beside the artwork rather than a full-width
toolbar. Navigation uses verified Adwaita icons for all five tabs:
`input-mouse-symbolic`, `preferences-system-symbolic`,
`display-brightness-symbolic`, `view-grid-symbolic`, and
`computer-symbolic`. The Side view uses a dedicated centered 336x233px
presentation container and a shared 26px left/right/bottom outer-margin token.

## Top-view callout review

The Top view now has six clickable presentation callouts. The final reviewed
capture is `gui-review/callouts-final-top.png`. Callout connectors use the
current transformed painted texture rectangle, with Fire Button on the left and
the five remaining labels in the exact Left, Wheel, Right, DPI+, DPI− order.
Right-side leaders share a 70px horizontal segment and use explicit diagonal
endpoints; Fire is a lowered direct leader aligned to the selector. The Side
view has no Top callout overlay.

Focused artwork-policy tests: 22 passed and 7 display-dependent widget tests
skipped. Complete suite: 917 passed and 7 skipped in 28.26 seconds. All
GUI and backend safety-audit tests passed. The isolated package build could not fetch
build dependencies in the offline environment; the equivalent
`python -m build --wheel --no-isolation` build succeeded and included the GUI
artwork and CSS resources.

## Real graphical screenshot review

The offline GUI was launched in the real X11 session with deterministic
`--demo-view top` and `--demo-view side` modes. Five Top/Side screenshot
iterations were captured and directly inspected under `gui-review/`. The loop
corrected the artwork-stage horizontal alignment, Side startup selector state,
and independent Top/Side presentation sizing. Final focused GUI tests: 22
passed and 7 display-dependent runtime tests skipped. Complete suite: 917
passed and 7 skipped; the non-isolated wheel build succeeded.

## GTK accessibility runtime fix

Navigation and hotspot labels now use `Gtk.Accessible.update_property` with
typed `GObject.Value` strings. Selected states use
`Gtk.Accessible.update_state` with typed boolean values. Focused GUI tests:
12 passed and 5 display-dependent widget tests skipped. Complete suite: 911
passed and 5 skipped in 30.21 seconds. GUI/backend safety audits passed and
the non-isolated wheel build succeeded.

## Offline button-assignment write plan

The offline-only serializer and immutable one-target plan are covered by 11
focused tests. They reuse the decoder allowlist, emit the exact 16-byte F3
template, preserve exact records, and expose no live transport or arbitrary
payload surface. The observed F1 report is represented as a fixed constant;
its minimal-sequence sufficiency remains unproven.

Current validation: focused write plus decoder tests 62 passed; relevant
protocol, address, profile, capture, validation, and safety tests 253 passed;
complete suite 858 passed in 33.69s; all nine static safety audits passed.

## Limited normal button-assignment writer

Offline mocked tests cover exact identity and permission checks, allowlisted
records, canonical raw-derived IDs, one F3, exact immediate readbacks,
duplicate-label restore, fail-closed evidence validation, no retry, no
automatic restore, and no finalizer/bracket reports. No live command was run.

Validation for this milestone: focused normal-writer, offline writer, and
decoder tests 94 passed; relevant tests 227 passed; complete suite 890 passed
in 32.13s. All ten
callable repository static audits passed, including the new guarded-assignment
audit.

The exact-raw restore correction adds `confirmed_assignment_raw(raw)` and
regression coverage for both duplicate-label records `90 00 1E 00` and
`90 00 04 00`. Restore evidence is validated and serialized using the exact
saved bytes; semantic labels are never used as unique identifiers.

The completed repair evidence confirmed that one F3 restored `90 00 1E 00`
from `90 00 04 00` before F1; F1 was not needed. The temporary incident repair
surface was removed afterward. The permanent exact-raw restore fix and
duplicate-label regression coverage remain. The evidence was saved in
`usb-captures/live-button-assignment-experiments/button_assignment_p1_b7_duplicate_label_repair_20260728T144009.099290Z`.

The writer and restore each use one exact case-sensitive `CONTINUE` word,
preceded by a full operation summary. Neither live path sends F1 or F5.

## Fire Key exact-record campaign

Fixture-backed tests cover the six exact Fire Key records and the repeated
Reset-to-Default confirmation:

```text
99 81 03 00  Fire Key: Left, 3 fires, 0 ms
99 81 04 01  Fire Key: Left, 4 fires, 10 ms
99 81 03 02  Fire Key: Left, 3 fires, 20 ms
99 82 03 01  Fire Key: Right, 3 fires, 10 ms
99 84 03 01  Fire Key: Middle, 3 fires, 10 ms
99 04 03 01  Fire Key: Keyboard A, 3 fires, 10 ms
```

The two reset readbacks are byte-for-byte identical across 4,604 captured
bytes. Each controlled sample changed only Profile 1 Button 4 at
`0x008E–0x0091`; all other assignment records and memory remained unchanged.
Fire decoding is exact-record-only. No generalized keyboard encoding,
unsupported delay range, writer, or persistence claim was added.

Validation for this milestone: focused button-assignment and Fire Key tests
51 passed; relevant decoder/profile/capture/validation/safety tests 242
passed; complete suite 847 passed in 29.58s; all eight repository static
safety audits passed.

## Controlled Combo/Report Rate button-assignment campaign

The fixture-backed button-assignment tests cover the four exact records from
the continuous saved pcap and matching readbacks:

```text
Combo Shift+Win: 8F 0A 00 00
Combo Shift+A:   8F 02 04 00
Report Rate+:    97 00 00 00
Report Rate-:    98 00 00 00
```

The campaign changed only Profile 1 ButtonAssigned_7 in all 100-record
comparisons. Report Rate± did not change the active polling-rate field,
selected DPI stage, lighting state, or any memory outside the target record.
Combo semantics remain exact-record-only; no generalized byte-field decoding
was added. Fire Key remains undecoded, with only its parameterized UI dialog
documented.

Validation for this milestone: focused button-assignment tests 39 passed;
relevant decoder/profile/capture/validation/safety tests 230 passed; complete
suite 835 passed in 32.39s; all eight repository static safety audits passed.

## Remaining lighting-mode batch

Offline analysis of the seven ordered snapshots confirms Wave `03 00 01`,
Flash `04 05 01`, and Breathing speed bytes Low `08`, Medium `05`, High `02`,
and UI-labelled Full Brightness `00`. Speed-only changes affect only
`0x044D`; RGB, mode byte, trailing byte, and brightness remain unchanged.
No live writers were added.

## Controlled Rainbow mode analysis

The offline Rainbow fixture has 556 feature transfers (193 F2 reads and 157
F3 writes). Pre/post reads each contain 4,604 bytes with identical coverage.
Only `0x044C: 01→02` and `0x044E: 01→03` changed; RGB and `0x044F=03` stayed
unchanged. Preview frame 3289 and Apply frame 3340 both write
`7B 2D D2 02 00 03`; persistence is confirmed by the post-read. No Rainbow
live writer was added.

The Rainbow → Full Brightness guarded recovery is offline-tested only. It
requires source triplet `7B 2D D2 02 00 03`, target `7B 2D D2 01 00 01`,
confirmation `WRITE 0449 RAINBOW TO FULL`, exact Report-2 F3/F1 templates,
and mandatory six-byte read-back. No live execution was performed.

Command:

```text
pytest -q
........................................................................ [ 38%]
........................................................................ [ 76%]
.............................................                            [100%]
189 passed in 0.39s
```

Environment: Python 3.14.6, pytest 8.4.2.

## Exact passing tests

```text
tests/test_breathing_speed.py::test_official_breathing_sample_validates_and_round_trips[NM12_BreathLow.pfd-8-8]
tests/test_breathing_speed.py::test_official_breathing_sample_validates_and_round_trips[NM13_BreathMed.pfd-5-5]
tests/test_breathing_speed.py::test_official_breathing_sample_validates_and_round_trips[NM14_BreathHigh.pfd-2-2]
tests/test_breathing_speed.py::test_official_breathing_sample_validates_and_round_trips[NM15_BreathFull.pfd-0-0]
tests/test_breathing_speed.py::test_breathing_speed_enumeration_decodes[NM12_BreathLow.pfd-8-8]
tests/test_breathing_speed.py::test_breathing_speed_enumeration_decodes[NM13_BreathMed.pfd-5-5]
tests/test_breathing_speed.py::test_breathing_speed_enumeration_decodes[NM14_BreathHigh.pfd-2-2]
tests/test_breathing_speed.py::test_breathing_speed_enumeration_decodes[NM15_BreathFull.pfd-0-0]
tests/test_breathing_speed.py::test_setting_breathing_speed_reproduces_official_field_change_only[NM12_BreathLow.pfd-8-8]
tests/test_breathing_speed.py::test_setting_breathing_speed_reproduces_official_field_change_only[NM13_BreathMed.pfd-5-5]
tests/test_breathing_speed.py::test_setting_breathing_speed_reproduces_official_field_change_only[NM14_BreathHigh.pfd-2-2]
tests/test_breathing_speed.py::test_setting_breathing_speed_reproduces_official_field_change_only[NM15_BreathFull.pfd-0-0]
tests/test_breathing_speed.py::test_setting_breathing_speed_does_not_alter_unrelated_data
tests/test_breathing_speed.py::test_unsupported_breathing_speed_is_rejected
tests/test_breathing_speed.py::test_unknown_stored_breathing_speed_is_preserved_losslessly
tests/test_breathing_speed.py::test_common_dpi_resave_mutation_is_not_breathing_speed_dependent
tests/test_brightness.py::test_official_brightness_sample_validates_and_round_trips[NM05_BrightOff.pfd-0-0]
tests/test_brightness.py::test_official_brightness_sample_validates_and_round_trips[NM06_BrightLow.pfd-1-1]
tests/test_brightness.py::test_official_brightness_sample_validates_and_round_trips[NM07_BrightMed.pfd-2-2]
tests/test_brightness.py::test_official_brightness_sample_validates_and_round_trips[NM08_BrightHigh.pfd-3-3]
tests/test_brightness.py::test_brightness_enumeration_decodes[NM05_BrightOff.pfd-0-0]
tests/test_brightness.py::test_brightness_enumeration_decodes[NM06_BrightLow.pfd-1-1]
tests/test_brightness.py::test_brightness_enumeration_decodes[NM07_BrightMed.pfd-2-2]
tests/test_brightness.py::test_brightness_enumeration_decodes[NM08_BrightHigh.pfd-3-3]
tests/test_brightness.py::test_setting_brightness_reproduces_official_field_change_only[NM05_BrightOff.pfd-0-0]
tests/test_brightness.py::test_setting_brightness_reproduces_official_field_change_only[NM06_BrightLow.pfd-1-1]
tests/test_brightness.py::test_setting_brightness_reproduces_official_field_change_only[NM07_BrightMed.pfd-2-2]
tests/test_brightness.py::test_setting_brightness_reproduces_official_field_change_only[NM08_BrightHigh.pfd-3-3]
tests/test_brightness.py::test_setting_brightness_does_not_alter_unrelated_profile_data
tests/test_brightness.py::test_unsupported_brightness_is_rejected
tests/test_brightness.py::test_unknown_stored_brightness_is_preserved_losslessly
tests/test_brightness.py::test_common_dpi_resave_mutation_is_not_brightness_dependent
tests/test_checksum_actions.py::test_additive_checksum
tests/test_checksum_actions.py::test_profile_struct_round_trip
tests/test_checksum_actions.py::test_action_constructors[record0-4-keyboard down]
tests/test_checksum_actions.py::test_action_constructors[record1-4-keyboard up]
tests/test_checksum_actions.py::test_action_constructors[record2-6-delay 5000]
tests/test_checksum_actions.py::test_action_constructors[record3-1-mouse left down]
tests/test_checksum_actions.py::test_action_constructors[record4-1-mouse right up]
tests/test_checksum_actions.py::test_action_constructors[record5-0-zero/padding]
tests/test_checksum_actions.py::test_delay_limits_and_quantization
tests/test_checksum_actions.py::test_unknown_record_is_lossless
tests/test_delay_separation.py::test_default_inserted_delay_500_ms_is_accepted
tests/test_delay_separation.py::test_default_inserted_delay_999_ms_is_accepted
tests/test_delay_separation.py::test_default_inserted_delay_1000_ms_is_rejected
tests/test_delay_separation.py::test_validator_rejects_out_of_range_default_inserted_delay
tests/test_delay_separation.py::test_action_delay_500_ms_encoding
tests/test_delay_separation.py::test_action_delay_5000_ms_encoding
tests/test_delay_separation.py::test_action_delay_655350_ms_is_accepted
tests/test_delay_separation.py::test_action_delay_above_655350_ms_is_rejected
tests/test_delay_separation.py::test_action_delay_not_divisible_by_10_ms_is_rejected
tests/test_delay_separation.py::test_round_trip_preserves_default_and_action_delay_independently
tests/test_fixed_samples.py::test_fixed_file_round_trips_byte_exact[04_Key1_Repeat3R.pfd]
tests/test_fixed_samples.py::test_fixed_file_round_trips_byte_exact[05_Key1_Repeat3_5sR.MSMACRO]
tests/test_fixed_samples.py::test_fixed_file_round_trips_byte_exact[06_Key1_WhileHeld_5sR.pfd]
tests/test_fixed_samples.py::test_fixed_file_round_trips_byte_exact[08_Keys12_DefDelayR.pfd]
tests/test_fixed_samples.py::test_fixed_file_round_trips_byte_exact[09_Keys12_RecDelayR.pfd]
tests/test_fixed_samples.py::test_fixed_file_round_trips_byte_exact[10_Keys12_NoDelayR.MSMACRO]
tests/test_fixed_samples.py::test_fixed_file_round_trips_byte_exact[NM00_Base.pfd]
tests/test_fixed_samples.py::test_fixed_macro_pfd_is_complete_and_assignment_valid[04_Key1_Repeat3R.pfd-3-04_Key1_Repeat3]
tests/test_fixed_samples.py::test_fixed_macro_pfd_is_complete_and_assignment_valid[06_Key1_WhileHeld_5sR.pfd-5-06_Key1_WhileHeld_5s]
tests/test_fixed_samples.py::test_fixed_macro_pfd_is_complete_and_assignment_valid[08_Keys12_DefDelayR.pfd-7-08_Keys12_DefDelay]
tests/test_fixed_samples.py::test_fixed_macro_pfd_is_complete_and_assignment_valid[09_Keys12_RecDelayR.pfd-8-09_Keys12_RecDelay]
tests/test_fixed_samples.py::test_fixed_macro_file_is_complete_and_valid[05_Key1_Repeat3_5sR.MSMACRO]
tests/test_fixed_samples.py::test_fixed_macro_file_is_complete_and_valid[10_Keys12_NoDelayR.MSMACRO]
tests/test_fixed_samples.py::test_fixed_explicit_5000_ms_macro
tests/test_fixed_samples.py::test_fixed_no_auto_delay_editor_output
tests/test_fixed_samples.py::test_default_500_encoding_is_distinct_from_explicit_5000
tests/test_fixed_samples.py::test_recorded_and_no_auto_delay_modes_are_distinct
tests/test_fixed_samples.py::test_fixed_and_while_held_modes_are_distinct
tests/test_fixed_samples.py::test_nm00_base_is_approved_complete_baseline
tests/test_fixed_samples.py::test_original_malformed_pfds_remain_negative[04_Key1_Repeat3.pfd-button-count]
tests/test_fixed_samples.py::test_original_malformed_pfds_remain_negative[06_Key1_WhileHeld_5s.pfd-button-count]
tests/test_fixed_samples.py::test_original_malformed_pfds_remain_negative[08_Keys12_DefDelay.pfd-button-count]
tests/test_fixed_samples.py::test_original_malformed_pfds_remain_negative[09_Keys12_RecDelay.pfd-button-count]
tests/test_fixed_samples.py::test_original_malformed_macros_remain_negative[05_Key1_Repeat3_5s.MSMACRO]
tests/test_fixed_samples.py::test_original_malformed_macros_remain_negative[10_Keys12_NoDelay.MSMACRO]
tests/test_polling_rate.py::test_official_polling_sample_validates_and_round_trips[NM01_Poll125.pfd-8-125]
tests/test_polling_rate.py::test_official_polling_sample_validates_and_round_trips[NM02_Poll250.pfd-4-250]
tests/test_polling_rate.py::test_official_polling_sample_validates_and_round_trips[NM03_Poll500.pfd-2-500]
tests/test_polling_rate.py::test_official_polling_sample_validates_and_round_trips[NM04_Poll1000.pfd-1-1000]
tests/test_polling_rate.py::test_polling_enumeration_decodes[NM01_Poll125.pfd-8-125]
tests/test_polling_rate.py::test_polling_enumeration_decodes[NM02_Poll250.pfd-4-250]
tests/test_polling_rate.py::test_polling_enumeration_decodes[NM03_Poll500.pfd-2-500]
tests/test_polling_rate.py::test_polling_enumeration_decodes[NM04_Poll1000.pfd-1-1000]
tests/test_polling_rate.py::test_setting_rate_reproduces_official_field_change[NM01_Poll125.pfd-8-125]
tests/test_polling_rate.py::test_setting_rate_reproduces_official_field_change[NM02_Poll250.pfd-4-250]
tests/test_polling_rate.py::test_setting_rate_reproduces_official_field_change[NM03_Poll500.pfd-2-500]
tests/test_polling_rate.py::test_setting_rate_reproduces_official_field_change[NM04_Poll1000.pfd-1-1000]
tests/test_polling_rate.py::test_setting_polling_rate_does_not_alter_unrelated_profile_data
tests/test_polling_rate.py::test_unsupported_polling_rate_is_rejected
tests/test_polling_rate.py::test_unknown_stored_polling_enum_is_preserved_losslessly
tests/test_polling_rate.py::test_common_dpi_resave_mutation_is_not_rate_dependent
tests/test_palette_color.py::test_official_colour_sample_validates_and_round_trips[NM09_ColorRed.pfd-65721-65721-rgb0]
tests/test_palette_color.py::test_official_colour_sample_validates_and_round_trips[NM10_ColorGreen.pfd-3454210-3454210-rgb1]
tests/test_palette_color.py::test_official_colour_sample_validates_and_round_trips[NM11_ColorBlue.pfd-16715534-16715534-rgb2]
tests/test_palette_color.py::test_primary_palette_colours_decode[NM09_ColorRed.pfd-65721-65721-rgb0]
tests/test_palette_color.py::test_primary_palette_colours_decode[NM10_ColorGreen.pfd-3454210-3454210-rgb1]
tests/test_palette_color.py::test_primary_palette_colours_decode[NM11_ColorBlue.pfd-16715534-16715534-rgb2]
tests/test_palette_color.py::test_setting_colour_reproduces_official_field_change_only[NM09_ColorRed.pfd-65721-65721-rgb0]
tests/test_palette_color.py::test_setting_colour_reproduces_official_field_change_only[NM10_ColorGreen.pfd-3454210-3454210-rgb1]
tests/test_palette_color.py::test_setting_colour_reproduces_official_field_change_only[NM11_ColorBlue.pfd-16715534-16715534-rgb2]
tests/test_palette_color.py::test_setting_colour_does_not_alter_unrelated_profile_data
tests/test_palette_color.py::test_unknown_raw_colour_is_preserved_losslessly
tests/test_palette_color.py::test_unsupported_palette_colour_is_rejected
tests/test_palette_color.py::test_complete_palette_matches_static_ldcfg_table
tests/test_palette_color.py::test_common_dpi_resave_mutation_is_not_colour_dependent
tests/test_led_mode.py::test_official_led_mode_sample_validates_and_round_trips[NM16_ModeBreathing.pfd-0-0]
tests/test_led_mode.py::test_official_led_mode_sample_validates_and_round_trips[NM17_ModeFull.pfd-1-1]
tests/test_led_mode.py::test_official_led_mode_sample_validates_and_round_trips[NM18_ModeRainbow.pfd-2-2]
tests/test_led_mode.py::test_official_led_mode_sample_validates_and_round_trips[NM19_ModeWave.pfd-3-3]
tests/test_led_mode.py::test_official_led_mode_sample_validates_and_round_trips[NM20_ModeFlash.pfd-4-4]
tests/test_led_mode.py::test_official_led_mode_sample_validates_and_round_trips[NM21_ModeOff.pfd-5-5]
tests/test_led_mode.py::test_led_mode_enumeration_decodes[NM16_ModeBreathing.pfd-0-0]
tests/test_led_mode.py::test_led_mode_enumeration_decodes[NM17_ModeFull.pfd-1-1]
tests/test_led_mode.py::test_led_mode_enumeration_decodes[NM18_ModeRainbow.pfd-2-2]
tests/test_led_mode.py::test_led_mode_enumeration_decodes[NM19_ModeWave.pfd-3-3]
tests/test_led_mode.py::test_led_mode_enumeration_decodes[NM20_ModeFlash.pfd-4-4]
tests/test_led_mode.py::test_led_mode_enumeration_decodes[NM21_ModeOff.pfd-5-5]
tests/test_led_mode.py::test_setting_led_mode_reproduces_official_field_change_only[NM16_ModeBreathing.pfd-0-0]
tests/test_led_mode.py::test_setting_led_mode_reproduces_official_field_change_only[NM17_ModeFull.pfd-1-1]
tests/test_led_mode.py::test_setting_led_mode_reproduces_official_field_change_only[NM18_ModeRainbow.pfd-2-2]
tests/test_led_mode.py::test_setting_led_mode_reproduces_official_field_change_only[NM19_ModeWave.pfd-3-3]
tests/test_led_mode.py::test_setting_led_mode_reproduces_official_field_change_only[NM20_ModeFlash.pfd-4-4]
tests/test_led_mode.py::test_setting_led_mode_reproduces_official_field_change_only[NM21_ModeOff.pfd-5-5]
tests/test_led_mode.py::test_setting_led_mode_does_not_alter_unrelated_data
tests/test_led_mode.py::test_unsupported_led_mode_is_rejected
tests/test_led_mode.py::test_unknown_stored_led_mode_is_preserved_losslessly
tests/test_led_mode.py::test_led_full_brightness_is_independent_from_other_controls
tests/test_led_mode.py::test_all_six_lighting_modes_are_distinct_and_use_led_mode_only
tests/test_led_mode.py::test_common_dpi_tail_is_save_normalization_not_led_mode
tests/test_generation.py::test_generate_key_1_once
tests/test_generation.py::test_generate_keys_1_and_2
tests/test_generation.py::test_generate_keys_1_through_5
tests/test_generation.py::test_generate_ctrl_qwert
tests/test_generation.py::test_generate_mouse_clicks[12_MouseLeftClick-1]
tests/test_generation.py::test_generate_mouse_clicks[13_MouseRightClick-2]
tests/test_generation.py::test_generate_mixed_keyboard_mouse
tests/test_generation.py::test_generate_loop_modes[04_Key1_Repeat3-0-3]
tests/test_generation.py::test_generate_loop_modes[06_Key1_WhileHeld_5s-1-1]
tests/test_generation.py::test_generate_loop_modes[07_Key1_Toggle_5s-3-1]
tests/test_generation.py::test_generate_5000_ms_delay
tests/test_roundtrip.py::test_controlled_inventory_count
tests/test_roundtrip.py::test_every_controlled_file_exact_round_trip
tests/test_roundtrip.py::test_complete_macro_models_have_expected_dimensions
tests/test_roundtrip.py::test_pfd_truncations_are_preserved
tests/test_roundtrip.py::test_macroset_exact_round_trip_and_slots
tests/test_roundtrip.py::test_pfd_macro_assignment_is_in_memory_only
tests/test_roundtrip.py::test_macroset_edit_is_in_memory_only
tests/test_resave_normalization.py::test_noop_resave_validates_and_round_trips
tests/test_resave_normalization.py::test_noop_resave_semantic_fields_match_baseline
tests/test_resave_normalization.py::test_noop_resave_has_only_confirmed_dpi_mutation
tests/test_resave_normalization.py::test_prior_batches_have_same_shape_but_session_variant_byte
tests/test_resave_normalization.py::test_feature_setters_do_not_reproduce_resave_mutation[polling]
tests/test_resave_normalization.py::test_feature_setters_do_not_reproduce_resave_mutation[brightness]
tests/test_resave_normalization.py::test_feature_setters_do_not_reproduce_resave_mutation[colour]
tests/test_resave_normalization.py::test_feature_setters_do_not_reproduce_resave_mutation[breathing]
tests/test_resave_normalization.py::test_feature_models_are_identical_across_noop_resave
tests/test_selected_dpi_stage.py::test_official_selected_dpi_sample_validates_and_round_trips[NM22_DPISelected1.pfd-0-1]
tests/test_selected_dpi_stage.py::test_official_selected_dpi_sample_validates_and_round_trips[NM23_DPISelected2.pfd-1-2]
tests/test_selected_dpi_stage.py::test_official_selected_dpi_sample_validates_and_round_trips[NM24_DPISelected3.pfd-2-3]
tests/test_selected_dpi_stage.py::test_official_selected_dpi_sample_validates_and_round_trips[NM25_DPISelected4.pfd-3-4]
tests/test_selected_dpi_stage.py::test_official_selected_dpi_sample_validates_and_round_trips[NM26_DPISelected5.pfd-4-5]
tests/test_selected_dpi_stage.py::test_selected_dpi_stage_decodes[NM22_DPISelected1.pfd-0-1]
tests/test_selected_dpi_stage.py::test_selected_dpi_stage_decodes[NM23_DPISelected2.pfd-1-2]
tests/test_selected_dpi_stage.py::test_selected_dpi_stage_decodes[NM24_DPISelected3.pfd-2-3]
tests/test_selected_dpi_stage.py::test_selected_dpi_stage_decodes[NM25_DPISelected4.pfd-3-4]
tests/test_selected_dpi_stage.py::test_selected_dpi_stage_decodes[NM26_DPISelected5.pfd-4-5]
tests/test_selected_dpi_stage.py::test_setting_selected_stage_reproduces_official_field_change_only[NM22_DPISelected1.pfd-0-1]
tests/test_selected_dpi_stage.py::test_setting_selected_stage_reproduces_official_field_change_only[NM23_DPISelected2.pfd-1-2]
tests/test_selected_dpi_stage.py::test_setting_selected_stage_reproduces_official_field_change_only[NM24_DPISelected3.pfd-2-3]
tests/test_selected_dpi_stage.py::test_setting_selected_stage_reproduces_official_field_change_only[NM25_DPISelected4.pfd-3-4]
tests/test_selected_dpi_stage.py::test_setting_selected_stage_reproduces_official_field_change_only[NM26_DPISelected5.pfd-4-5]
tests/test_selected_dpi_stage.py::test_all_stage_values_and_enable_flags_remain_unchanged
tests/test_selected_dpi_stage.py::test_setting_selected_stage_does_not_alter_unrelated_data
tests/test_selected_dpi_stage.py::test_unknown_selected_stage_is_preserved_losslessly
tests/test_selected_dpi_stage.py::test_invalid_selected_stage_is_rejected[0]
tests/test_selected_dpi_stage.py::test_invalid_selected_stage_is_rejected[6]
tests/test_selected_dpi_stage.py::test_dpi_tail_is_isolated_from_selected_stage
tests/test_validation_cli.py::test_complete_macro_validates
tests/test_validation_cli.py::test_mouse_count_anomaly_is_preserved_and_warned
tests/test_validation_cli.py::test_truncated_macro_reports_missing_blob
tests/test_validation_cli.py::test_macroset_validates
tests/test_validation_cli.py::test_complete_pfd_slot_relationship
tests/test_validation_cli.py::test_truncated_pfd_is_rejected_but_round_trips
tests/test_validation_cli.py::test_cli_inspect_validate_decode
tests/test_validation_cli.py::test_validator_reports_unknown_record
tests/test_validation_cli.py::test_validator_reports_missing_release
tests/test_validation_cli.py::test_validator_rejects_count_over_capacity
```

## Round-trip result

All 44 controlled files reproduce their original bytes exactly when parsed and
encoded unchanged. This includes:

- all complete PFD, MSDB, MSMACRO, and MacroSet files;
- truncated PFDs 04, 06, 08, and 09;
- truncated MSMACROs 05 and 10.

No sample failed byte-exact round-trip.

All seven files under `ProfilesMacros/Fixed` also pass byte-exact round-trip
and their complete structures validate. The six malformed originals remain in
the original 44-file round-trip test and are additionally retained as negative
validation fixtures.

The complete macro count is 26 because each of 14 standalone MSDB files is
complete and 12 of 14 MSMACRO files are complete.

## Preserved anomalies

`12_MouseLeftClick` retains `Count=8`, its two mouse records, and six zero
records inside Count. Validation emits six `zero-within-count` warnings but
does not normalize the value.

Truncated files remain byte-identical. Validation reports missing fields or
button sections instead of repairing them.

Unknown action records retain their original three bytes and produce a warning.
Unreleased keyboard or mouse states produce validation errors.

## Official-structure generation

Bodies created entirely through Python match official structures for:

- key 1 once;
- keys 1 and 2;
- keys 1 through 5;
- Left Ctrl held across Q, W, E, R, T;
- left click, including the official Count anomaly;
- right click;
- key 1, left click, key 2;
- fixed count 3;
- repeat while held;
- toggle until pressed again;
- one 5000 ms delay.

## Corrected recreated fixtures

All seven recreated files are structurally complete:

| File | Bytes | Result |
|---|---:|---|
| `04_Key1_Repeat3R.pfd` | 10,671 | valid 21-section PFD; button 7 → slot 3 |
| `05_Key1_Repeat3_5sR.MSMACRO` | 579 | complete macro; checksum `BF` |
| `06_Key1_WhileHeld_5sR.pfd` | 10,676 | valid 21-section PFD; button 7 → slot 5 |
| `08_Keys12_DefDelayR.pfd` | 10,674 | valid 21-section PFD; button 7 → slot 7 |
| `09_Keys12_RecDelayR.pfd` | 10,674 | valid 21-section PFD; button 7 → slot 8 |
| `10_Keys12_NoDelayR.MSMACRO` | 578 | complete macro; checksum `9F` |
| `NM00_Base.pfd` | 10,654 | valid non-macro baseline |

Each complete PFD has 43 group keys, 20 semantic button sections, 20
189-byte button payloads, one 408-byte DPI payload, and valid checksums.

The corrected explicit-delay macro confirms:

```text
84 1E 00  keyboard 1 down
04 1E 00  keyboard 1 up
06 F4 01  5000 ms delay
```

It stores `Count=3`, `LoopType=0`, `LoopTime=3`, and checksum `BF`.

The recreated no-automatic-delay macro stores `DelayType=2`, `Count=7`, and
three `06 01 00` records, confirming 30 ms of editor-minimum delays remain.

### PFD macro-body evidence boundary

PFD files store macro name and slot, not the action body or loop/delay
metadata. The corrected active MSMACRO files corresponding to recreated PFDs
04, 06, 08, and 09 were not included under `Fixed`.

The older slot-7 companion contains `DefDelayTime=250`, whereas the recreated
test setup specifies 500 ms. Therefore `08_Keys12_DefDelayR.pfd` proves a
complete slot-7 assignment but cannot itself prove the new 500 ms macro body.
The compiler test confirms that 500 ms encodes as `06 32 00`, distinct from
5000 ms `06 F4 01`; this distinction is not a byte comparison against a
supplied corrected slot-7 MSMACRO.

Likewise, exact rerecorded timings for corrected PFD 09 are unavailable from
the PFD alone. The older referenced companion contains 90, 860, and 70 ms
(1020 ms total), but it is not evidence that the recreated Windows session
produced the identical timing.

## Remaining blockers

The file core cannot yet generate every arbitrary non-macro profile setting
without a template. Remaining mappings include complete DPI conversion, LED
mode/breath/flash behavior, sensitivity, lift/angle, and non-macro button
functions. Polling rate, brightness, breathing speed, LED mode, and the fixed
LED palette are now fully mapped.

Additional file-level uncertainties are middle-click file confirmation,
delay/UI and 63-record boundaries, MacroSet deletion/reordering behavior, and
the exact MSDB export lifecycle.

USB transfer, commit, timing, acknowledgments, and readback remain outside this
prototype and require separate live-protocol validation.

## Delay-model separation amendment

The model now names and validates two independent concepts:

| Concept | Model/API | Range |
|---|---|---|
| Default inserted delay | `DefDelayTime`; `set_default_inserted_delay_ms()` | 0–999 ms |
| Action delay | type-6 record; `ActionRecord.action_delay_ms()` | 0–655,350 ms in 10 ms steps |

Ten focused tests confirm:

- default inserted delays 500 and 999 are accepted;
- 1000 is rejected by both the setter and file validator;
- Action delays encode 500 as `06 32 00` and 5000 as `06 F4 01`;
- 655,350 is accepted and the next 10 ms step is rejected;
- non-10-ms-aligned Action delays are rejected without rounding;
- parse/encode and default-setting edits preserve Action delay records
  independently.

No existing sample became invalid. Compatibility aliases remain for the
earlier `ActionRecord.delay()` and `default_delay_ms` API names.

## Polling-rate controlled tests

All four official samples are complete, checksum-valid, macro-free PFDs and
round-trip byte-for-byte:

| File | Stored enum | Rate |
|---|---:|---:|
| `NM01_Poll125.pfd` | 8 | 125 Hz |
| `NM02_Poll250.pfd` | 4 | 250 Hz |
| `NM03_Poll500.pfd` | 2 | 500 Hz |
| `NM04_Poll1000.pfd` | 1 | 1000 Hz |

Sixteen tests confirm official validation/round trips, complete decoding,
setting all four rates, isolation from unrelated data, unsupported-rate
rejection, unknown-enum preservation, and the common DPI resave mutation.

Among the four polling samples, only the one-character `PollingRate` value
differs. Relative to `NM00_Base.pfd`, all four also share the same
rate-independent DPI mutation at data offsets 298–300:

```text
00 00 00 -> 0C 48 BD
checksum F2 -> 03
```

The 500 Hz file retains enum 2 yet has the same DPI mutation, proving that the
mutation is not caused by selecting a different polling rate. The API setter
therefore changes only `PollingRate`.

## Brightness controlled tests

All four official samples are 10,654-byte, 21-section, 43-GROUP-key PFDs with
twenty semantic button sections, all twenty button blobs and the DPI blob
present, valid additive checksums, no macro assignments, and exact
parse/encode round trips:

| File | Stored `LedState1` | Level |
|---|---:|---|
| `NM05_BrightOff.pfd` | 0 | Off |
| `NM06_BrightLow.pfd` | 1 | Low |
| `NM07_BrightMed.pfd` | 2 | Medium |
| `NM08_BrightHigh.pfd` | 3 | High |

Sixteen tests cover validation/round trips, all four enum decodes, official
field reproduction, unrelated-data isolation, unsupported-value rejection,
lossless unknown-value preservation, and separation of the common DPI resave
mutation.

Among these four files, only `LedState1` changes. At zero-based raw file offset
233 its ASCII byte is `30`, `31`, `32`, or `33`. `FlashState1=5`,
`BreathState1=5`, `LedMode1=0`, `LedColor1=255`, `PollingRate=2`, DPI,
buttons, and all other data are identical between the four files.

Against `NM00_Base.pfd`, every brightness file has the same unrelated DPI
mutation at data offsets 298–300 (`00 00 00 -> 0C 48 18`) and checksum change
`F2 -> 5E`. In the raw PFD these are offsets 964–968 (ASCII `00000` to
`C4818`) and 1183–1184 (`F2` to `5E`). The Medium file retains the baseline
brightness enum 2 while still containing this mutation, proving it is not
brightness encoding. The API setter changes only `LedState1`.

## LED palette controlled and static tests

The three official PFDs are structurally complete, checksum-valid, macro-free,
and round-trip exactly:

| File | Decimal `LedColor1` | Hex `COLORREF` | RGB |
|---|---:|---:|---|
| `NM09_ColorRed.pfd` | 65721 | `000100B9` | 185,0,1 |
| `NM10_ColorGreen.pfd` | 3454210 | `0034B502` | 2,181,52 |
| `NM11_ColorBlue.pfd` | 16715534 | `00FF0F0E` | 14,15,255 |

Fourteen tests cover validation and exact round trips, primary decoding,
setter reproduction, isolation from all unrelated profile data, unknown raw
value preservation, unsupported palette-value rejection, exact recovery of
the 33-entry static table, and the common DPI resave mutation.

The representation is Windows `COLORREF`: integer `0x00BBGGRR`; when packed
as a little-endian dword its bytes are `RR GG BB 00`. There is no alpha byte;
the high byte is unused and zero.

The three files differ from one another only in the decimal `LedColor1` text,
whose value begins at zero-based raw offset 277 and has variable length.
Against `NM00_Base.pfd`, every file also has the same unrelated DPI mutation:
data offsets 298–300 change `00 00 00 -> 0C 48 C9` and checksum `F2 -> 0F`.
The mutation is identical for all colours and is excluded from the setter.

Static verification reads 33 little-endian dwords at `ldcfg.exe` file offset
`0x1B4898`. Disassembly independently shows the editor uses table address
`0x005B6098` and bounds its grid loop at `0x21`. The complete table is
documented in `FORMAT_SPEC.md`; no unobserved colours were guessed.

## Breathing-speed controlled tests

All four official files are 10,654-byte, 21-section, 43-GROUP-key PFDs with
the DPI blob, all twenty button blobs, and all twenty semantic button sections
present. Every checksum is valid, no macro is assigned, and every file
round-trips exactly:

| File | Stored `BreathState1` | Editor value |
|---|---:|---|
| `NM12_BreathLow.pfd` | 8 | Low |
| `NM13_BreathMed.pfd` | 5 | Medium |
| `NM14_BreathHigh.pfd` | 2 | High |
| `NM15_BreathFull.pfd` | 0 | Full Brightness |

Sixteen tests cover validation/round trips, all four enum decodes, official
field reproduction, isolation from unrelated settings, unsupported-value
rejection, lossless unknown-value preservation, and the common DPI artifact.

Among the four samples, only the one-byte ASCII value of `BreathState1` at
zero-based raw offset 264 changes (`38`, `35`, `32`, or `30`). Colour,
brightness, LED mode, Flash, polling, DPI, buttons, macros, and all other data
are identical between the files.

Against `NM00_Base.pfd`, every sample shares a DPI data change at offsets
298–300 (`00 00 00 -> 0C 48 7F`) and checksum `F2 -> C5`. In raw PFD text,
the actual differences are offsets 964–968 (`00000 -> C487F`) and 1183–1184.
The full encoded three-byte span begins at 963. The Medium sample retains the
baseline enum 5 while still having the mutation, proving it is an unrelated
editor-resave artifact. The setter changes only `BreathState1`.

## No-op resave regression

`NM00_Resaved.pfd` is a complete 10,654-byte PFD with 21 sections, 43 GROUP
keys, all expected structures, valid checksums, no macros, and an exact
parse/encode round trip.

Compared with `NM00_Base.pfd`, every field except `DPIStageValue` is
byte-identical. Within its 408-byte payload, only these bytes change:

```text
data[298]: 00 -> 0C
data[299]: 00 -> 48
data[300]: 00 -> 7F
checksum: F2 -> C5
```

The actual raw PFD differences are offsets 964–968 (`00000 -> C487F`) and
1183–1184 (`F2 -> C5`). The checksum is exactly the additive consequence of
the payload delta; no separate checksum anomaly exists.

Nine regression cases prove validation/round-trip behavior, equality of all
semantic settings, exact mutation scope, cross-batch variation, setter
isolation for polling/brightness/colour/breathing, and equality of all feature
models.

This no-change Save As formally classifies the recurring mutation as generic
official-editor save normalization. Across batches, the values are:

```text
polling     0C 48 BD
brightness  0C 48 18
colour      0C 48 C9
breathing   0C 48 7F
no-op save  0C 48 7F
```

The third byte is stable inside each observed batch but varies between
batches; the no-op save matches the immediately preceding breathing batch.
This is consistent with volatile or cached session state. Its precise source,
repeat-save stability, and any relationship to unknown DPI UI state remain
unresolved.

## LED-mode controlled tests

All six official samples are complete 10,654-byte PFDs with 21 sections, 43
GROUP keys, all expected blobs and valid checksums, no macro assignments, and
exact parse/encode round trips:

| File | Stored `LedMode1` | Mode |
|---|---:|---|
| `NM16_ModeBreathing.pfd` | 0 | Breathing |
| `NM17_ModeFull.pfd` | 1 | Full Brightness |
| `NM18_ModeRainbow.pfd` | 2 | Rainbow |
| `NM19_ModeWave.pfd` | 3 | Wave |
| `NM20_ModeFlash.pfd` | 4 | Flash |
| `NM21_ModeOff.pfd` | 5 | Off |

Twenty-four tests cover official validation/round trips, all six enum decodes,
setter reproduction, isolation, unsupported-value rejection, unknown-value
preservation, six-choice distinctness, distinct Full Brightness controls, and
DPI-tail classification.

Only the ASCII `LedMode1` digit at zero-based raw offset 220 is
mode-dependent: Breathing is `30`, Full Brightness `31`, Rainbow `32`, Wave
`33`, Flash `34`, and Off `35`. Colour, Brightness Level, Breathing Speed,
`FlashState1`, polling, buttons, macros, and understood DPI settings are
unchanged.

All six also contain the known no-op-resave tail:

```text
DPI data[298:301] = 0C 48 7F
checksum = C5
```

Against the clean baseline, this accounts for unrelated raw differences at
964–968 (`00000 -> C487F`) and 1183–1184 (`F2 -> C5`). Since the Breathing
file keeps baseline `LedMode1=0` while containing this tail, it cannot be mode
encoding. The setter changes only `LedMode1`.

The similarly named concepts are explicitly independent:

```text
LED Full Brightness mode:       LedMode1=1
Brightness Level High:          LedState1=3
Breathing Speed Full Brightness: BreathState1=0
```

The Full Brightness mode sample keeps `LedState1=2` and `BreathState1=5`
(both Medium).

Flash and Off require no secondary flags: their complete raw representations
are respectively `LedMode1=4, FlashState1=5` and
`LedMode1=5, FlashState1=5`. `FlashState1` is 5 for every mode. Consequently,
the valid state is a single enum in `LedMode1`; unknown values are preserved
and warned rather than coerced into a multi-field combination.

## Selected DPI-stage controlled tests

All five official files are complete 10,654-byte PFDs with 21 sections, 43
GROUP keys, all expected structures, valid checksums, no macros, and exact
parse/encode round trips.

| File | Stored GROUP `DPI` | Selected stage |
|---|---:|---:|
| `NM22_DPISelected1.pfd` | 0 | 1 |
| `NM23_DPISelected2.pfd` | 1 | 2 |
| `NM24_DPISelected3.pfd` | 2 | 3 |
| `NM25_DPISelected4.pfd` | 3 | 4 |
| `NM26_DPISelected5.pfd` | 4 | 5 |

Twenty-one tests cover validation/round trips, all five decodes, setter
reproduction, unchanged DPI tuples, unrelated-setting isolation, unknown raw
preservation, invalid stage 0/6 rejection, and DPI-tail isolation.

Selection is a zero-based integer in the semantic GROUP `DPI` field. Its
single ASCII byte at zero-based raw offset 303 is `30`, `31`, `32`, `33`, or
`34` for stages 1..5. No packed byte, bit mask, enable flag, `DPICurrentX/Y`
value, or derived duplicate changes with selection.

The packed tuples remain:

```text
(1,9,9,1), (1,19,19,1), (1,39,39,1),
(1,71,71,1), (1,106,106,1)
```

These retain the controlled 1000, 2000, 4000, 8200, and 16400 DPI stages.
All files also retain `DPIStageNum=5`, `DPICurrentX=14`, and
`DPICurrentY=14`.

Every file contains identical editor normalization at DPI data offsets
298–300 (`0C 48 7F`) with checksum `C5`. Against the clean baseline this is
the known raw difference at 964–968 and 1183–1184. It does not vary with
stage, and the setter neither generates nor alters it.

## Controlled DPI-value tests

All eight official samples are complete 10,654-byte PFDs with 21 sections,
43 GROUP keys, twenty semantic button sections, twenty button blobs, one
valid 408-byte DPI payload, no macro assignments, and exact byte-for-byte
round trips.

| Actual filename | Intended DPI | Raw X/Y | DPI checksum |
|---|---:|---:|---:|
| `NM27_DPI1_200.pfd` | 200 | 1 | `B5` |
| `NM28_DPI1_400.pfd` | 400 | 3 | `B9` |
| `NM29_DPI1_800.pfd` | 800 | 7 | `C1` |
| `NM30_DPI1_1600.pfd` | 1600 | 15 | `D1` |
| `NM31_DPI1_3200.pfd` | 3200 | 31 | `F1` |
| `NM32_DPI1_6400.pfd` | 6400 | 62 | `2F` |
| `NM33_DPI1_12000.pfd` | 12000 | 90 | `67` |
| `NM34_DPI1_16400.pfd` | 16400 | 106 | `87` |

Thirty-six new test cases cover all official validations/round trips,
decoding, setter reproduction, baseline values, the complete 106-value legal
domain, isolation of other stages/selection/enable flags/tail, rejected DPI
values and stage numbers, plus lossless unknown/asymmetric X/Y values.
The complete suite at that point passed 225 tests.

Only stage-1 data offsets 4..11 vary with the slider: X is the little-endian
dword at 4..7 and Y at 8..11. In the raw PFD text these dwords occupy offsets
375..382 and 383..390. All samples keep stage 2 selected (`DPI=1`), all
enable/flag dwords equal to 1, stages 2..5 unchanged, and
`DPICurrentX=DPICurrentY=14`.

The piecewise conversion implemented by `ldcfg.exe` is `(raw+1)*100` for
1..61, `(raw-30)*200` for 62..92, `(raw-51)*300` for 93..105, with raw 106
special-cased to 16400. Every sample also has the common independent
`0C 48 7F` tail at data offsets 298..300.

## Controlled DPI-stage enable tests

All five official samples are complete 10,654-byte PFDs with 21 sections,
43 GROUP keys, valid checksums, twenty button blobs/sections, no macro
assignments, and exact round trips. There are no validation errors.
`NM36_DPI2_Disabled.pfd` has the expected non-fatal
`selected-dpi-stage-disabled` warning because its saved selection remains
stage 2.

| File | Disabled stage | Enable data offset | Raw changed character |
|---|---:|---:|---:|
| `NM35_DPI1_Disabled.pfd` | 1 | 0 | 368 |
| `NM36_DPI2_Disabled.pfd` | 2 | 16 | 400 |
| `NM37_DPI3_Disabled.pfd` | 3 | 32 | 432 |
| `NM38_DPI4_Disabled.pfd` | 4 | 48 | 464 |
| `NM39_DPI5_Disabled.pfd` | 5 | 64 | 496 |

Each uint32 changes from `01 00 00 00` to `00 00 00 00`. Against the clean
baseline, every file also has the independent resave-tail changes at raw
offsets 964..968 (`00000 -> C487F`) and checksum text at 1183..1184
(`F2 -> C4`). Against `NM00_Resaved.pfd`, only the enable character and final
checksum nibble differ (`C5 -> C4`).

All X/Y values and tuple flags remain unchanged. `DPIStageNum=5`,
`DPICurrentX=14`, and `DPICurrentY=14` remain unchanged. All saved files also
retain `DPI=1`, contrary to the transient UI fallback described during sample
creation; that fallback is therefore not serialized in this controlled set.

Thirty-one new tests cover official validation/round trips, enable decoding,
exact setter reproduction, retained values/flags, selected stage and
`DPIStageNum`, exact raw differences, unknown raw preservation, warning
states, rejected arguments, and unrelated/tail isolation. The complete suite
passes 256 tests.

## Controlled acceleration tests

All three samples are complete 10,654-byte, 21-section PFDs with 43 GROUP
keys, valid checksums and blobs, twenty semantic button sections, no macros,
and exact byte-for-byte round trips.

| File | Slider placement | `Acceleration` |
|---|---|---:|
| `NM40_AccelerationMin.pfd` | fully left | 1 |
| `NM41_AccelerationMid.pfd` | approximate visual centre | 2 |
| `NM42_AccelerationMax.pfd` | fully right | 4 |

The clean baseline is also 1. The only acceleration-dependent raw byte is the
ASCII value at offset 133: baseline/minimum `31`, midpoint sample `32`, and
maximum `34`. No pointer, wheel, double-click, polling, DPI, lighting,
button, macro, or other field changes with acceleration.

All three saves contain the independent DPI-tail normalization at raw offsets
964..968 (`00000 -> C487F`) and checksum text change at 1183..1184
(`F2 -> C5`). The acceleration setter neither creates nor alters it.

Nineteen new tests cover validation/round trips, controlled and baseline
decoding, exact setter reproduction, acceptance of levels 1..4, invalid
requests, unknown raw preservation, exact byte offsets, and unrelated/tail
isolation. The complete suite passes 275 tests.

## Controlled pointer-speed tests

All three official profiles have 21 sections, 43 GROUP keys, all expected
blobs and button sections, valid checksums, no macros, and exact round trips.
The minimum file is 10,653 bytes because its one-digit value shortens the INI
by one byte; midpoint and maximum are 10,654 bytes.

| File | Slider placement | `MouseSensitivity` |
|---|---|---:|
| `NM43_PointerSpeedMin.pfd` | fully left | 1 |
| `NM44_PointerSpeedMid.pfd` | approximate centre | 10 |
| `NM45_PointerSpeedMax.pfd` | fully right | 20 |

The clean baseline is 10. Pointer-speed text starts at raw offset 70.
`MouseSpeedX/Y=10` and `XYSensitivityEnabled=0` remain unchanged; there is no
duplicate or derived field.

Each file has the independent `0C 48 7F` DPI tail with checksum `C5`. Its
complete hex span begins at raw offset 962 in the one-byte-shorter minimum
file and 963 in the other two. The setter preserves it.

Twenty new tests cover official validation/round trips, decoding, exact
setter reproduction, all levels 1..20, rejected inputs, lossless unknown
values, raw location/representation, field isolation, and DPI-tail isolation.
The complete suite passes 295 tests.

## Controlled scroll-speed tests

All three profiles have 21 sections, 43 GROUP keys, all expected blobs and
button sections, valid checksums, no macros, and exact round trips. Minimum is
10,654 bytes; midpoint and maximum are 10,655 bytes because their two-digit
values add one byte.

| File | Slider placement | `WheelScrollLines` |
|---|---|---:|
| `NM46_ScrollSpeedMin.pfd` | fully left | 1 |
| `NM47_ScrollSpeedMid.pfd` | approximate centre | 33 |
| `NM48_ScrollSpeedMax.pfd` | fully right | 63 |

The baseline is 1. The decimal value starts at raw offset 50. No acceleration,
pointer, double-click, polling, DPI, lighting, button, macro, or other
semantic field changes.

Each file contains the independent `0C 48 7F` DPI tail and checksum `C5`.
The complete tail starts at raw offset 963 for minimum and 964 for the
one-byte-longer midpoint/maximum files. The setter preserves it.

Twenty new tests cover official validation/round trips, decoding, exact
setter reproduction, all values 1..63, rejected inputs, lossless unknown
values, raw representation, unrelated-setting isolation, and DPI-tail
isolation. The complete suite passes 315 tests.

## Controlled double-click-speed tests

All three profiles have 21 sections, 43 GROUP keys, all expected blobs and
button sections, valid checksums, no macros, and exact round trips. Minimum
and midpoint are 10,654 bytes; maximum is 10,655 bytes because level `10`
adds one text byte.

| File | Placement | Level | Threshold |
|---|---|---:|---:|
| `NM49_DoubleClickMin.pfd` | fully left | 1 | 830 ms |
| `NM50_DoubleClickMid.pfd` | approximate centre | 5 | 550 ms |
| `NM51_DoubleClickMax.pfd` | fully right | 10 | 200 ms |

Baseline is level 5/550 ms. `DoubleClickSpeed` starts at raw offset 91.
No other semantic or packed state changes.

Each file contains the independent `0C 48 7F` DPI tail and checksum `C5`.
The full tail begins at raw offset 963 for minimum/midpoint and 964 for the
one-byte-longer maximum file.

Twenty new tests cover validation/round trips, level and millisecond decoding,
exact setters, all ten levels and the static timing formula, rejected inputs,
unknown preservation, raw representation, unrelated-setting isolation, and
DPI-tail isolation. The complete suite passes 335 tests.

## Controlled custom-colour test

`NM52_CustomColor_123456.pfd` is a complete 10,658-byte, 21-section profile
with 43 GROUP keys, valid checksums/blobs, twenty button sections, no macros,
and an exact round trip.

The official file stores `LedColor1=1193046` (`0x00123456`, little-endian
`56 34 12 00`). As COLORREF this decodes to RGB `(86,52,18)` / `#563412`,
not the entered RGB `(18,52,86)` / `#123456`. Correct COLORREF encoding is
`0x00563412`, decimal 5,649,426, bytes `12 34 56 00`.

Only `LedColor1` is custom-colour-dependent. The independent tail
`0C 48 7F` remains at DPI data offsets 298..300 with checksum `C5`. Its
complete raw span starts at offset 967 and checksum at 1187.

Twenty-seven tests cover the official file, exact picker quirk, semantic
RGB/COLORREF conversion, strict hex parsing, channel boundaries, invalid
inputs, palette compatibility, non-palette losslessness, field isolation,
and tail preservation. The complete suite passes 362 tests.
# Guarded LED-mode write experiment

The first offline-only guarded LED-mode milestone adds an exact Profile 1
six-byte prefix transition. No live operation was performed.

- Focused transport/capture/state/safety regression selection: 194 passed.
- Complete prototype suite: 563 passed.
- Dry run emits only the validated F2 pre-read, exact six-byte Report 2 F3,
  exact lighting F1, and F2 read-back.
- Every single-byte mutation of the permitted F3 is rejected.
- Source mismatch is rejected before the private write-capable backend opens.
- Immediate read-back must match all six bytes.
- No F5, Report 3/4/5 write, generic sender, retry, restoration, or
  noninteractive bypass exists.
- LED-mode history is stored separately from brightness history and remains
  pending until a complete saved live read confirms all six bytes offline.

Reverse-transition extension:

- Full Brightness → Breathing is confirmed persistent after unplug/replug.
- Breathing → Full Brightness now uses the exact reverse F3 and distinct typed
  confirmation while retaining every fixed-prefix safety boundary.
- Guarded LED-mode tests: 45 passed.
- Focused transport/capture/state/safety regression selection: 200 passed.
- Complete prototype suite: 569 passed.

## Controlled LED-colour transition

- Both native reads validate as 193-request, 4,604-byte complete reads with
  identical coverage and presence maps.
- Exactly `0x0449–0x044B` changed: direct device RGB `FF 00 00` →
  `0E 0F FF`.
- Preview and Apply carry identical blue bytes in the six-byte Profile 1
  lighting-prefix F3.
- Colour analysis and semantic inspection add no live colour writer.
- Focused lighting/transport/capture/state/safety selection: 216 passed.
- Complete prototype suite: 578 passed.

## Guarded LED-colour write experiment

- Only exact official Blue/Red device prefixes are constructible.
- Blue → Red is the sole live-authorised direction; Red → Blue is tested
  offline and rejected by the live CLI.
- Source mismatch is rejected before the private writer opens.
- The suffix `01 00 01`, address range, exact read-back, and absence of F5 are
  enforced.
- Guarded colour tests: 46 passed.
- Focused lighting/transport/capture/state/safety selection: 262 passed.
- Complete prototype suite: 624 passed.

Reverse colour extension:

- Blue → Red is confirmed persistent after unplug/replug.
- Red → Blue now uses the exact reverse F3 and distinct typed confirmation.
- The prior confirmed history is preserved and the reverse transition remains
  pending after immediate read-back.
- Guarded colour tests: 51 passed.
- Focused lighting/transport/capture/state/safety selection: 267 passed.
- Complete prototype suite: 629 passed.

## Controlled official Blue → Green transition

- Both native reads validate with identical 193-request/4,604-byte coverage.
- Exactly `0x0449–0x044B` changed: `0E 0F FF` → direct RGB `00 FF 01`.
- Two preview cycles were captured: transient `02 B5 34`, then selected Green;
  Apply and the post-read contain only selected Green.
- Green has semantic read support but is excluded from guarded live writes.
- Focused lighting/transport/capture/state/safety selection: 274 passed.
- Complete prototype suite: 636 passed.

## Guarded persistent Green → Blue experiment

- Only final persistent Green `00 FF 01 01 00 01` is accepted.
- Transient preview `02 B5 34 01 00 01` is absent from supported values and
  rejected by the report/source validators.
- Green → Blue is live-authorised; Blue → Green has no CLI option.
- Existing confirmed Red/Blue histories and the controlled Windows
  Blue → Green state evidence remain append-only.
- Guarded colour tests: 64 passed.
- Focused session/read/state/guard safety selection: 243 passed.
- Complete prototype suite: 649 passed.

## Guarded Official Blue → Official Green extension

- Exact Blue `0E 0F FF 01 00 01` is required before the private writer opens.
- Exact persistent Green `00 FF 01 01 00 01` is the only accepted target.
- Transient preview `02 B5 34 01 00 01` remains unsupported and rejected.
- Immediate exact read-back appends Blue → Green while preserving all prior
  colour history; power-cycle persistence remains pending.
- Guarded colour tests: 72 passed.
- Focused session/read/state/guard safety selection: 251 passed.
- Complete prototype suite: 657 passed.
- Brightness, LED-mode, LED-colour, and read-transport static audits: passed.

## Controlled custom colour #7B2DD2

- Both live reads contain 193 valid requests and 4,604 bytes with identical
  address coverage and presence maps.
- Exactly `0x0449–0x044B` changed: `00 FF 01` → `D2 2D 7B`.
- The expected native-RGB sequence `7B 2D D2` was not written by Windows.
  The official custom picker reversed entered channels before writing
  `D2 2D 7B`; mouse memory still interprets that raw value directly as RGB.
- Final preview frame 2323 and Apply frame 2374 agree exactly.
- Mode/suffix `01 00 01` and brightness `03` remained unchanged.
- Arbitrary custom colour-field storage is confirmed; arbitrary native writes
  remain disabled.
- Custom-colour analysis/colour/lighting/guarded/session/state focused tests:
  266 passed.
- Complete prototype suite: 664 passed.
- All brightness, LED-mode, LED-colour, and read-transport safety audits:
  passed.

## Finite visual colour cycle

- Added six offline tests for the immutable 20-colour plan, exact direct-RGB
  reports, Enter/ABORT and Y/N/SKIP gating, observations, Ctrl+C, state
  diagnostics, dry-run, and safety auditing.
- Focused visual-cycle tests: 6 passed.
- Live execution: not performed.

## Visual-cycle interaction fix

- Fixed case-sensitive visual-answer handling and added explicit re-prompting
  for malformed initial, visual, and continuation responses.
- Step 20 no longer requests a continuation; EOF/Ctrl+C produce an aborted
  diagnostic and never mark completion.
- Visual-cycle focused tests: 11 passed.
- Complete prototype suite after the fix: pending final run below.

## True LED Mode Off analysis

- Added offline capture/read analysis for Full Brightness → true Off.
- True-Off analysis tests: 3 passed.
- Lighting-state mapping now decodes device mode raw `00` as Off while live
  true-Off writing remains disabled.
- Complete prototype suite after this analysis: 745 passed.

## Guarded True Off → Full Brightness recovery

- Added only the exact `7b 2d d2 00 00 00` → `7b 2d d2 01 00 01`
  direction; Full Brightness → Off remains disabled.
- Guarded mode and lighting tests: 58 passed.
- Complete prototype suite after this implementation: 749 passed.
- Static safety audits: passed; no live operation performed.

## Completed visual-cycle diagnostic recovery

- Recovered the complete 20-step run from
  `visual_colour_cycle_20260727T134322.992138Z`.
- Repaired only the summary metadata/state commit; the 20-step evidence files
  remain preserved.
- Visual diagnostics are now independently authoritative from ordinary colour
  transition history and are validated for counts, exact read-back, and
  semantic prefix consistency.
- Visual/state focused tests: 13 passed before the complete-suite run.

## First guarded arbitrary custom-colour milestone

- The original semantic BGR conversion was disproven by visible live output.
- Native strict `#RRGGBB` now maps directly to device `RR GG BB`.
- Corrected recovery accepts only target `#FF0000` from source `#0000FF`.
- Corrected write prefix: `FF 00 00 01 00 01`; `0x044F` is excluded.
- Custom state is independent and persistence remains pending after immediate
  read-back.
- Guarded custom-colour tests: 55 passed.
- Focused custom/palette/lighting/session/capture/state tests: 329 passed.
- Complete prototype suite: 727 passed.
- Custom-colour, palette-colour, LED-mode, brightness, and read-transport
  static audits: passed.

## Native RGB semantic correction and recovery guard

- The persisted `00 00 FF` write visibly produced blue, proving mouse memory
  is direct RGB.
- Custom state migrated to schema v2 with an explicit correction record;
  original raw reports, timestamps, result path, and persistence evidence are
  retained.
- Recovery is restricted to `#0000FF` → `#FF0000`, exact target prefix
  `FF 00 00 01 00 01`.
- Guarded custom-colour tests: 56 passed.
- Focused colour/lighting/guard/session/capture/state tests: 330 passed.
- Complete prototype suite: 728 passed.
- All custom-colour, palette-colour, mode, brightness, and read audits:
  passed.

## Second controlled custom colour #12A7E4

- Both live reads contain 193 valid requests and 4,604 bytes with identical
  coverage and presence maps.
- Exactly `0x0449–0x044B` changed: `D2 2D 7B` → `E4 A7 12`.
- Final preview frame 2263 and Apply frame 3490 agree exactly.
- The two asymmetric samples confirm semantic `#RRGGBB` → device
  `BB GG RR` for custom-picker values.
- Captured palette bytes remain explicit mappings; arbitrary live writing is
  still disabled.
- New/prior colour, lighting, guarded/session/capture/state focused tests:
  274 passed.
- Complete prototype suite: 672 passed.
- All brightness, LED-mode, LED-colour, and read-transport safety audits:
  passed.

## Guarded lighting cycle offline integration

- Added focused offline coverage for the fixed seven-stage guarded lighting
  cycle, exact reports, transport sequencing, restoration, artifacts, CLI
  barriers, and static safety audit.
- Focused guarded-lighting-cycle tests: 19 passed.
- Module offline CLI safety audit: passed.
- Existing repository static safety-audit tests plus guarded-lighting-cycle
  audit coverage: 26 passed.
- Complete prototype suite: 782 passed in 30.80s.
- No live operation performed.

## Functional offline application milestone (2026-11-18)

- Environment: `env -u WAYLAND_DISPLAY xvfb-run -a -s "-screen 0 1600x1200x24" python3 -m pytest -q`
  (GUI tests run under a virtual display; no tests skipped).
- Baseline before this milestone: 1165 passed, 4 failed. Three failures were
  layout-timing GUI tests (also failing on the native display); one was a
  D-Bus application-ID collision.
- Complete suite after this milestone: **1193 passed, 0 failed, 0 skipped**.
- New focused suites: `test_button_functions.py`, `test_workspace.py`,
  `test_apply_plan.py`, `test_gui_application.py`,
  `test_gui_buttons_settings.py`, rewritten `test_gui_dpi.py` and
  `test_gui_lighting.py`, and macro GUI tests ported to the persistent
  library.
- Static safety audits: 11/11 passed, including the strengthened
  transitive-closure GUI audit.
- Fixed during testing:
  - library drag payloads lost their prefix, so drops were ignored;
  - library rows were rebuilt on dirty/warning changes, swallowing clicks;
  - the lifted drag preview was 18 px narrower under Adwaita (margin math
    mixed width APIs);
  - an Apply-preview DPI address off by one stage.

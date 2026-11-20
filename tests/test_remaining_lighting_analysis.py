from venus_profile.remaining_lighting_analysis import analyse, SNAPSHOTS


def test_remaining_snapshots_validate_and_states_are_exact():
    result = analyse()
    assert len(result["snapshot_validation"]["snapshots"]) == 7
    assert all(item["read_count"] == 193 and item["returned_byte_count"] == 4604
               for item in result["snapshot_validation"]["snapshots"].values())
    states = [item["lighting_state"] for item in result["snapshot_validation"]["snapshots"].values()]
    assert states == [
        "7b 2d d2 01 00 01 03", "7b 2d d2 03 00 01 03",
        "7b 2d d2 04 05 01 03", "7b 2d d2 02 05 01 03",
        "7b 2d d2 02 08 01 03", "7b 2d d2 02 02 01 03",
        "7b 2d d2 02 00 01 03",
    ]


def test_pairwise_diffs_isolate_modes_and_speed_byte():
    rows = analyse()["pairwise_diffs"]
    assert [[(x["address"], x["previous"], x["new"]) for x in row["changed"]]
            for row in rows] == [
        [("0x044C", "01", "03")],
        [("0x044C", "03", "04"), ("0x044D", "00", "05")],
        [("0x044C", "04", "02")],
        [("0x044D", "05", "08")],
        [("0x044D", "08", "02")],
        [("0x044D", "02", "00")],
    ]


def test_capture_has_two_mode_writes_per_stage_and_matching_f1():
    capture = analyse()["capture"]
    assert (capture["feature_transfers"], capture["f2_reads"], capture["f3_writes"]) == (6040, 2509, 942)
    writes = capture["mode_writes"]
    assert len(writes) == 12
    assert [w["payload"] for w in writes[::2]] == [
        "7b 2d d2 03 00 01", "7b 2d d2 04 05 01",
        "7b 2d d2 02 05 01", "7b 2d d2 02 08 01",
        "7b 2d d2 02 02 01", "7b 2d d2 02 00 01",
    ]
    assert [w["phase"] for w in writes[::2]] == ["preview"] * 6
    assert len(capture["f1_commands"]) == 54
    correlations = capture["transition_correlations"]
    assert [(x["before"], x["after"], x["preview_f3_frame"],
             x["apply_f3_frame"], x["preview_f1_frame"], x["apply_f1_frame"])
            for x in correlations] == [
        ("full_brightness", "wave", 2025, 2065, 2045, 2361),
        ("wave", "flash", 31074, 31135, 31094, 31431),
        ("flash", "breathing_default", 42075, 42115, 42095, 42411),
        ("breathing_default", "breathing_low", 46963, 47002, 46983, 47298),
        ("breathing_low", "breathing_high", 53313, 53352, 53333, 53648),
        ("breathing_high", "breathing_ui_full_brightness", 59103, 59143, 59123, 59443),
    ]
    assert all(x["preview_f1"].startswith("02 f1 02 04") for x in correlations)

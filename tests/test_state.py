from state import load_last_heartbeat, save_last_heartbeat


def test_save_and_load_roundtrip(tmp_path):
    p = tmp_path / "state"
    save_last_heartbeat(p, 12345.6)
    assert load_last_heartbeat(p) == 12345.6


def test_load_missing_returns_none(tmp_path):
    assert load_last_heartbeat(tmp_path / "nope") is None


def test_load_corrupt_returns_none(tmp_path):
    p = tmp_path / "state"
    p.write_text("not a number")
    assert load_last_heartbeat(p) is None


def test_none_path_is_noop():
    save_last_heartbeat(None, 1.0)
    assert load_last_heartbeat(None) is None


def test_save_creates_parent_dirs(tmp_path):
    p = tmp_path / "subdir" / "nested" / "state"
    save_last_heartbeat(p, 1.0)
    assert p.exists()
    assert load_last_heartbeat(p) == 1.0


def test_save_overwrites(tmp_path):
    p = tmp_path / "state"
    save_last_heartbeat(p, 1.0)
    save_last_heartbeat(p, 2.0)
    assert load_last_heartbeat(p) == 2.0


def test_no_tmp_file_left_behind(tmp_path):
    # Atomic write uses a .tmp file that must be renamed away, not left over.
    p = tmp_path / "state"
    save_last_heartbeat(p, 1.0)
    assert not (tmp_path / "state.tmp").exists()
"""S5: the shared atomic-write helper that all persistence layers now route through."""

from lake_rise.fsutil import atomic_write_text


def test_writes_content_and_creates_parent_dirs(tmp_path):
    target = tmp_path / "nested" / "dir" / "state.json"
    atomic_write_text(target, '{"x": 1}')
    assert target.read_text() == '{"x": 1}'
    assert target.parent.is_dir()


def test_overwrites_existing_file(tmp_path):
    target = tmp_path / "state.json"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")
    assert target.read_text() == "second"


def test_leaves_no_tmp_litter(tmp_path):
    target = tmp_path / "state.json"
    atomic_write_text(target, "payload")
    assert list(tmp_path.glob("*.tmp")) == []


def test_accepts_str_path(tmp_path):
    target = tmp_path / "state.json"
    atomic_write_text(str(target), "ok")
    assert target.read_text() == "ok"

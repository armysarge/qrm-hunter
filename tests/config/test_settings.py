from qrm_hunter.config.settings import Settings, SettingsStore


def test_missing_file_gives_defaults(tmp_path):
    assert SettingsStore(tmp_path / "s.toml").load() == Settings()


def test_round_trip(tmp_path):
    store = SettingsStore(tmp_path / "s.toml")
    s = Settings(region=2, mains_hz=60, colormap="viridis", last_device_id="rtlsdr:0", last_tune_hz=14_074_000)
    store.save(s)
    assert store.load() == s


def test_none_fields_are_not_written(tmp_path):
    store = SettingsStore(tmp_path / "s.toml")
    store.save(Settings())
    text = store.path.read_text(encoding="utf-8")
    assert "token" not in text
    assert "last_device_id" not in text


def test_corrupt_file_falls_back_to_defaults_and_is_kept(tmp_path):
    path = tmp_path / "s.toml"
    path.write_text("region = [[[", encoding="utf-8")
    assert SettingsStore(path).load() == Settings()
    assert (tmp_path / "s.toml.bad").exists()
    assert not path.exists()


def test_invalid_value_falls_back_to_defaults(tmp_path):
    path = tmp_path / "s.toml"
    path.write_text("region = 9\n", encoding="utf-8")
    assert SettingsStore(path).load() == Settings()


def test_save_creates_parent_directories(tmp_path):
    store = SettingsStore(tmp_path / "a" / "b" / "s.toml")
    store.save(Settings(theme="light"))
    assert store.load().theme == "light"


def test_unreadable_file_falls_back_to_defaults(tmp_path, monkeypatch, caplog):
    path = tmp_path / "s.toml"
    path.write_text("region = 2\n", encoding="utf-8")

    def locked(self, *a, **kw):
        raise PermissionError(13, "The process cannot access the file", str(self))

    monkeypatch.setattr(type(path), "read_text", locked)
    with caplog.at_level("WARNING"):
        assert SettingsStore(path).load() == Settings()
    assert "cannot read settings file" in caplog.text
    monkeypatch.undo()
    assert path.read_text(encoding="utf-8") == "region = 2\n"  # left alone, not moved aside


def test_corrupt_file_that_cannot_be_moved_aside_falls_back(tmp_path, monkeypatch, caplog):
    import os

    path = tmp_path / "s.toml"
    path.write_text("region = [[[", encoding="utf-8")

    def busy(src, dst):
        raise PermissionError(32, "The process cannot access the file because it is being used", str(src))

    monkeypatch.setattr(os, "replace", busy)
    with caplog.at_level("WARNING"):
        assert SettingsStore(path).load() == Settings()
    assert "could not be moved aside" in caplog.text
    assert path.exists() and not (tmp_path / "s.toml.bad").exists()


def test_save_after_unreadable_load_backs_up_original(tmp_path, monkeypatch):
    path = tmp_path / "s.toml"
    path.write_text('region = 2\ntoken = "keep-me"\n', encoding="utf-8")
    store = SettingsStore(path)

    def locked(self, *a, **kw):
        raise PermissionError(13, "The process cannot access the file", str(self))

    monkeypatch.setattr(type(path), "read_text", locked)
    assert store.load() == Settings()
    monkeypatch.undo()
    store.save(Settings(region=3))
    assert 'token = "keep-me"' in (tmp_path / "s.toml.bak").read_text(encoding="utf-8")
    assert store.load().region == 3
    store.save(Settings(region=1))  # flag cleared: no second backup overwrite needed
    assert 'token = "keep-me"' in (tmp_path / "s.toml.bak").read_text(encoding="utf-8")


def test_save_after_unreadable_load_skips_when_backup_fails(tmp_path, monkeypatch, caplog):
    import shutil

    path = tmp_path / "s.toml"
    path.write_text("region = 2\n", encoding="utf-8")
    store = SettingsStore(path)

    def locked(self, *a, **kw):
        raise PermissionError(13, "The process cannot access the file", str(self))

    monkeypatch.setattr(type(path), "read_text", locked)
    store.load()
    monkeypatch.undo()

    def busy(*a, **kw):
        raise PermissionError(32, "busy", str(path))

    monkeypatch.setattr(shutil, "copy2", busy)
    with caplog.at_level("WARNING"):
        store.save(Settings(region=3))  # must not raise, must not overwrite
    assert "not overwriting" in caplog.text
    assert path.read_text(encoding="utf-8") == "region = 2\n"


def test_recordings_dir_and_callsign():
    from pydantic import ValidationError

    from qrm_hunter.config.settings import SettingsPatch

    s = Settings(recordings_dir="D:/QRM", callsign="zs6shz/p")
    assert s.callsign == "ZS6SHZ/P" and s.recordings_dir == "D:/QRM"
    assert Settings().recordings_dir == "" and Settings().callsign == ""
    for bad in ("ZS6 SHZ", "x" * 13, "zs6<shz>"):
        try:
            SettingsPatch(callsign=bad)
        except ValidationError:
            continue
        raise AssertionError(bad)

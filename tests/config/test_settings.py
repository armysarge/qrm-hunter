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

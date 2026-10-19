import json

import app_settings
import audit_log


def test_audit_log_records_outcome_and_identity(tmp_path, monkeypatch):
    log_path = tmp_path / "audit.log"
    monkeypatch.setattr(audit_log, "LOG_PATH", str(log_path))

    audit_log.log_event("TEST\\operator", "disable_account", "jdoe", "CN=Jane Doe | denied", outcome="failed")

    line = log_path.read_text(encoding="utf-8").strip()
    assert "actor=TEST\\operator" in line
    assert "action=disable_account" in line
    assert "outcome=failed" in line
    assert "target=jdoe" in line


def test_settings_drop_unknown_keys_and_never_store_password(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", str(settings_path))
    settings_path.write_text(json.dumps({"server": "dc01", "use_ssl": False, "password": "x"}), encoding="utf-8")

    loaded = app_settings.load_settings()
    assert loaded["server"] == "dc01"
    assert "use_ssl" not in loaded and "password" not in loaded

    app_settings.save_settings({**loaded, "password": "secret"})
    assert "password" not in json.loads(settings_path.read_text(encoding="utf-8"))


def test_unreadable_settings_fall_back_to_defaults(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", str(settings_path))
    settings_path.write_text("{not json", encoding="utf-8")
    assert app_settings.load_settings() == app_settings.DEFAULTS

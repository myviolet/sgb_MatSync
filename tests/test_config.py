import pytest

from mysql_table_sync.config import Settings


def test_settings_loads_urls_from_environment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("SOURCE_DATABASE_URL", "mysql+pymysql://source")
    monkeypatch.setenv("TARGET_DATABASE_URL", "mysql+pymysql://target")
    monkeypatch.setenv("BATCH_SIZE", "500")

    settings = Settings.from_env(tmp_path / "missing.env")

    assert settings.source_table == "SAP_RW_Material_Move"
    assert settings.target_table == "record_frozen_data"
    assert settings.source_primary_key == "ID"
    assert settings.source_excluded_ids == ("OnlyKey",)
    assert settings.batch_size == 500


def test_settings_parses_multiple_excluded_source_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("SOURCE_DATABASE_URL", "mysql+pymysql://source")
    monkeypatch.setenv("TARGET_DATABASE_URL", "mysql+pymysql://target")
    monkeypatch.setenv("SOURCE_EXCLUDED_IDS", "OnlyKey, BAD_ROW ")

    settings = Settings.from_env(tmp_path / "missing.env")

    assert settings.source_excluded_ids == ("OnlyKey", "BAD_ROW")


def test_settings_rejects_missing_database_url(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("SOURCE_DATABASE_URL", raising=False)
    monkeypatch.delenv("TARGET_DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="SOURCE_DATABASE_URL"):
        Settings.from_env(tmp_path / "missing.env")


def test_settings_rejects_non_positive_batch_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("SOURCE_DATABASE_URL", "mysql+pymysql://source")
    monkeypatch.setenv("TARGET_DATABASE_URL", "mysql+pymysql://target")
    monkeypatch.setenv("BATCH_SIZE", "0")

    with pytest.raises(ValueError, match="positive integer"):
        Settings.from_env(tmp_path / "missing.env")

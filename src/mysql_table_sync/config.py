"""Environment-backed application settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    source_database_url: str
    target_database_url: str
    source_table: str = "SAP_RW_Material_Move"
    target_table: str = "record_frozen_data"
    source_primary_key: str = "ID"
    source_excluded_ids: tuple[str, ...] = ("OnlyKey",)
    batch_size: int = 1000
    log_level: str = "INFO"
    log_file: str = "logs/mysql_table_sync.log"
    log_max_bytes: int = 10_485_760
    log_backup_count: int = 5

    @classmethod
    def from_env(cls, env_file: str | Path = ".env") -> "Settings":
        load_dotenv(env_file, override=False)
        source_url = os.getenv("SOURCE_DATABASE_URL", "").strip()
        target_url = os.getenv("TARGET_DATABASE_URL", "").strip()
        if not source_url or not target_url:
            raise ValueError(
                "SOURCE_DATABASE_URL and TARGET_DATABASE_URL must be set in the environment or .env"
            )

        try:
            batch_size = int(os.getenv("BATCH_SIZE", "1000"))
        except ValueError as error:
            raise ValueError("BATCH_SIZE must be a positive integer") from error
        if batch_size <= 0:
            raise ValueError("BATCH_SIZE must be a positive integer")

        try:
            log_max_bytes = int(os.getenv("LOG_MAX_BYTES", "10485760"))
            log_backup_count = int(os.getenv("LOG_BACKUP_COUNT", "5"))
        except ValueError as error:
            raise ValueError("LOG_MAX_BYTES and LOG_BACKUP_COUNT must be integers") from error
        if log_max_bytes <= 0 or log_backup_count < 0:
            raise ValueError("LOG_MAX_BYTES must be positive and LOG_BACKUP_COUNT non-negative")

        return cls(
            source_database_url=source_url,
            target_database_url=target_url,
            source_table=os.getenv("SOURCE_TABLE", "SAP_RW_Material_Move").strip(),
            target_table=os.getenv("TARGET_TABLE", "record_frozen_data").strip(),
            source_primary_key=os.getenv("SOURCE_PRIMARY_KEY", "ID").strip(),
            source_excluded_ids=tuple(
                value.strip()
                for value in os.getenv("SOURCE_EXCLUDED_IDS", "OnlyKey").split(",")
                if value.strip()
            ),
            batch_size=batch_size,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            log_file=os.getenv("LOG_FILE", "logs/mysql_table_sync.log").strip(),
            log_max_bytes=log_max_bytes,
            log_backup_count=log_backup_count,
        )

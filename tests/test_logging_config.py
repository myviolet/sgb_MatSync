import logging

from mysql_table_sync.config import Settings
from mysql_table_sync.logging_config import configure_logging


def test_configure_logging_writes_utf8_rotating_log(tmp_path) -> None:
    log_path = tmp_path / "logs" / "sync.log"
    settings = Settings(
        "sqlite://source",
        "sqlite://target",
        log_file=str(log_path),
        log_max_bytes=1024,
        log_backup_count=2,
    )
    root_logger = logging.getLogger()
    previous_handlers = root_logger.handlers[:]

    try:
        configure_logging(settings)
        logging.getLogger("sync-test").info("同步测试 batch_committed")
        for handler in root_logger.handlers:
            handler.flush()

        assert log_path.exists()
        contents = log_path.read_text(encoding="utf-8")
        assert "同步测试 batch_committed" in contents
    finally:
        for handler in root_logger.handlers[:]:
            handler.close()
        root_logger.handlers.clear()
        for handler in previous_handlers:
            root_logger.addHandler(handler)

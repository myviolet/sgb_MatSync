"""PyInstaller entry point for the Linux standalone executable."""

from mysql_table_sync.cli import main


if __name__ == "__main__":
    raise SystemExit(main())

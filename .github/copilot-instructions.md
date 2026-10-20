# Workspace Guidance

- Keep the MySQL synchronization code under `src/mysql_table_sync` and tests under `tests`.
- Read connection settings from environment variables or `.env`; never commit credentials.
- Do not perform target writes until field mapping, key columns, and duplicate/deletion behavior are explicitly defined.
- Keep database access behind SQLAlchemy engines and the service layer; favor bounded batches for table data.
- Run `pytest` after changes to synchronization logic and keep operational behavior documented in `README.md`.

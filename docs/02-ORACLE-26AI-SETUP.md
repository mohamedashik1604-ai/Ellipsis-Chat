# Oracle Database 26ai setup

The backend uses **Oracle Database 26ai**. The config defaults to `DB_USER=sandbox`, `DB_DSN=localhost:1521/FREEPDB1` and reads `DB_PASSWORD` from the environment.

## DDL sequence

1. Connect with a privileged account (system schema used) to create users in the intended PDB (`FREEPDB1`).
2. Create database user `sandbox` with a securely provisioned password; grant `CREATE SESSION`, `CREATE TABLE`, `CREATE VIEW`, `CREATE SEQUENCE` and a quota in the `USERS` tablespace.
3. Switch to `CURRENT_SCHEMA=sandbox`, then create the six application and document tables, constraints and relational indexes.
4. Load source PDF file separately using the ingestion workflow.

```sql
-- Illustrative DBA command; replace placeholder using secure provisioning
CREATE USER sandbox IDENTIFIED BY "REPLACE_WITH_STRONG_PASSWORD"
  DEFAULT TABLESPACE users QUOTA UNLIMITED ON users;
GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE SEQUENCE TO sandbox;
```

## Database connectivity

Config uses `python-oracledb` with a lazy connection pool: `min=2`, `max=10`, `increment=1`, `wait_timeout=10000` milliseconds, idle timeout 300 seconds, statement cache 50. It sets `oracledb.defaults.fetch_lobs=False` to reduce extra LOB fetch round trips.

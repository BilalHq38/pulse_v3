# Deployment Notes

## Database Schema

Run [backend/sql_schema.sql](/C:/Users/mbila/OneDrive/Documents/Work/pulse-engine/backend/sql_schema.sql:1) as the canonical PostgreSQL bootstrap file. The old repository-root `schema.sql` snapshot has been retired to prevent drift.

## pgvector on Amazon RDS PostgreSQL

`backend/sql_schema.sql` already attempts `CREATE EXTENSION IF NOT EXISTS vector` before any table that can use vector storage. For Amazon RDS PostgreSQL, the target instance must be on an engine version that supports pgvector and the database user must have permission to install the extension.

Verified references:
- AWS extension support matrix: https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-extensions.html
- AWS extension usage guide: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Appendix.PostgreSQL.CommonDBATasks.Extensions.html

### Console steps

1. Open `Amazon RDS` in the AWS Console.
2. Open `Databases` and select the target PostgreSQL instance.
3. In `Configuration`, confirm the engine is `PostgreSQL` and the engine version is one of the RDS versions that supports pgvector for your major release.
4. Connect to the database as `rds_superuser`, or on PostgreSQL 13+ as a role that can install trusted extensions if your RDS permissions allow it.
5. Run:

```sql
SHOW rds.extensions;
CREATE EXTENSION IF NOT EXISTS vector;
```

6. Verify installation:

```sql
SELECT extname, extversion
FROM pg_extension
WHERE extname = 'vector';
```

### AWS CLI verification steps

1. Confirm the instance engine and version:

```bash
aws rds describe-db-instances \
  --db-instance-identifier <db-instance-identifier> \
  --query "DBInstances[0].[Engine,EngineVersion]" \
  --output table
```

2. Connect with `psql` and verify that RDS exposes the extension list:

```sql
SHOW rds.extensions;
```

3. Enable the extension in the target database:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

If `vector` is missing from `SHOW rds.extensions;`, upgrade to an RDS PostgreSQL engine version that supports pgvector before running the schema.

.PHONY: db-migrate db-rollback db-history db-current db-heads db-grant-schemas

db-migrate:
	alembic upgrade head

db-rollback:
	alembic downgrade -1

db-history:
	alembic history --verbose

db-current:
	alembic current

db-heads:
	alembic heads

# Run after db-migrate to grant schema permissions to pulse_app role
db-grant-schemas:
	psql "$$DATABASE_URL" -f scripts/grant_schema_permissions.sql

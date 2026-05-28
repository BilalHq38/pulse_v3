import uvicorn

from shared.config import service_port
from shared.app_factory import create_service_app
from services.auth_service.routes import routers
from services.bootstrap import (
    bootstrap_auth_security,
    bootstrap_demo_accounts,
    bootstrap_roles,
    bootstrap_signup_primitives,
    bootstrap_super_admin,
)

app = create_service_app(
    service_name="auth-service",
    title="Pulse Engine Auth Service",
    routers=routers,
    db_schema="auth_service",
    startup_tasks=(
        bootstrap_roles,
        bootstrap_auth_security,
        bootstrap_signup_primitives,
        bootstrap_super_admin,
        bootstrap_demo_accounts,
    ),
)


if __name__ == "__main__":
    uvicorn.run("services.auth_service.main:app", host="0.0.0.0", port=service_port(8001))

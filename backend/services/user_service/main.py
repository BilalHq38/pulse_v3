import uvicorn

from shared.app_factory import create_service_app
from shared.config import service_port
from services.bootstrap import bootstrap_roles, bootstrap_signup_primitives
from services.user_service.routes import routers

app = create_service_app(
    service_name="user-service",
    title="Pulse Engine User Service",
    routers=routers,
    db_schema="user_service",
    startup_tasks=(bootstrap_roles, bootstrap_signup_primitives),
)


if __name__ == "__main__":
    uvicorn.run("services.user_service.main:app", host="0.0.0.0", port=service_port(8002))

import uvicorn

from shared.app_factory import create_service_app
from shared.config import service_port
from services.analytics_service.routes import routers

app = create_service_app(
    service_name="analytics-service",
    title="Pulse Engine Analytics Service",
    routers=routers,
    db_schema="analytics_service",
)


if __name__ == "__main__":
    uvicorn.run(
        "services.analytics_service.main:app",
        host="0.0.0.0",
        port=service_port(8006),
    )

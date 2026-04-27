import uvicorn

from shared.app_factory import create_service_app
from shared.config import service_port
from services.notification_service.routes_registry import routers

app = create_service_app(
    service_name="notification-service",
    title="Pulse Engine Notification Service",
    routers=routers,
    db_schema="notification_service",
)


if __name__ == "__main__":
    uvicorn.run(
        "services.notification_service.main:app",
        host="0.0.0.0",
        port=service_port(8008),
    )

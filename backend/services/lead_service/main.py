import uvicorn

from shared.app_factory import create_service_app
from shared.config import service_port
from services.lead_service.routes import routers

app = create_service_app(
    service_name="lead-service",
    title="Pulse Engine Lead Service",
    routers=routers,
    db_schema="lead_service",
)


if __name__ == "__main__":
    uvicorn.run("services.lead_service.main:app", host="0.0.0.0", port=service_port(8004))

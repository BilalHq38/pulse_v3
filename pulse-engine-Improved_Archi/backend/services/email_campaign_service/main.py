"""Email campaign microservice entry point."""

import uvicorn

from shared.app_factory import create_service_app
from shared.config import service_port
from services.email_campaign_service.routes import routers
from services.email_campaign_service.service import bootstrap_email_campaign_schema

app = create_service_app(
    service_name="email-campaign-service",
    title="Pulse Engine Email Campaign Service",
    routers=routers,
    db_schema="email_campaign_service",
    startup_tasks=(bootstrap_email_campaign_schema,),
)


if __name__ == "__main__":
    uvicorn.run(
        "services.email_campaign_service.main:app",
        host="0.0.0.0",
        port=service_port(8013),
    )

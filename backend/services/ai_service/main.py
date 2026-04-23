import uvicorn

from shared.app_factory import create_service_app
from shared.config import service_port
from services.ai_service.routes import routers
from services.bootstrap import bootstrap_ai_runtime

app = create_service_app(
    service_name="ai-service",
    title="Pulse Engine AI Service",
    routers=routers,
    db_schema="ai_service",
    startup_tasks=(bootstrap_ai_runtime,),
)


if __name__ == "__main__":
    uvicorn.run("services.ai_service.main:app", host="0.0.0.0", port=service_port(8005))

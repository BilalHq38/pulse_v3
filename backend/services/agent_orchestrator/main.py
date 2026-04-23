import uvicorn

from agent_orchestrator.bootstrap import bootstrap_agent_orchestrator
from shared.app_factory import create_service_app
from shared.config import service_port
from services.agent_orchestrator.routes import router

app = create_service_app(
    service_name="agent-orchestrator-service",
    title="Pulse Engine Agent Orchestrator Service",
    routers=(router,),
    db_schema="agent_orchestrator",
    startup_tasks=(bootstrap_agent_orchestrator,),
)


if __name__ == "__main__":
    uvicorn.run(
        "services.agent_orchestrator.main:app",
        host="0.0.0.0",
        port=service_port(8009),
    )

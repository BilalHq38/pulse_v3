import uvicorn
import socketio

from core.socket import sio
from shared.app_factory import create_service_app
from shared.config import service_port
from services.bootstrap import bootstrap_customer_runtime
from services.customer_service.email_intake import bootstrap_email_imap_intake
from services.customer_service.routes import routers

fastapi_app = create_service_app(
    service_name="customer-service",
    title="Pulse Engine Customer Service",
    routers=routers,
    db_schema="customer_service",
    startup_tasks=(bootstrap_customer_runtime, bootstrap_email_imap_intake),
)
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)


if __name__ == "__main__":
    uvicorn.run("services.customer_service.main:app", host="0.0.0.0", port=service_port(8003))

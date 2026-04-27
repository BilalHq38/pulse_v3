import uvicorn

from shared.app_factory import create_service_app
from shared.config import service_port
from services.super_admin_service.routes import router as super_admin_router

app = create_service_app(
    service_name="super-admin-service",
    title="Pulse Engine Super Admin Service",
    routers=(),
    db_schema="super_admin_service",
)
app.include_router(super_admin_router, prefix="/api")
app.include_router(super_admin_router)


if __name__ == "__main__":
    uvicorn.run(
        "services.super_admin_service.main:app",
        host="0.0.0.0",
        port=service_port(8011),
    )

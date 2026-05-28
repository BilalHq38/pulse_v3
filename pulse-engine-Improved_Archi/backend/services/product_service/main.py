import uvicorn

from shared.app_factory import create_service_app
from shared.config import service_port
from services.product_service.routes import routers

app = create_service_app(
    service_name="product-service",
    title="Pulse Engine Product Service",
    routers=routers,
    db_schema="product_service",
)


if __name__ == "__main__":
    uvicorn.run("services.product_service.main:app", host="0.0.0.0", port=service_port(8007))

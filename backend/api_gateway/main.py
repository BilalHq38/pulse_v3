import uvicorn

from api_gateway.app import app
from shared.config import service_port

__all__ = ["app"]


if __name__ == "__main__":
    uvicorn.run("api_gateway.main:app", host="0.0.0.0", port=service_port(8000))

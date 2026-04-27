from routers.auth import router as legacy_auth_router
from shared.router_loader import clone_router

auth_router = clone_router(legacy_auth_router)
routers = (auth_router,)

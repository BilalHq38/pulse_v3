from routers.analytics import router as legacy_analytics_router
from shared.router_loader import clone_router

analytics_router = clone_router(legacy_analytics_router)
routers = (analytics_router,)

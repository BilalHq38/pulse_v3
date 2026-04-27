from routers.misc import notifications_router as legacy_notifications_router
from shared.router_loader import clone_router
from services.notification_service.routes import router as notification_router

notifications_router = clone_router(legacy_notifications_router)
routers = (notifications_router, notification_router)

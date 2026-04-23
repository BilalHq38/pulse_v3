from routers.billing import router as legacy_billing_router
from routers.misc import security_router as legacy_security_router
from routers.settings import router as legacy_settings_router
from routers.users import router as legacy_users_router
from shared.router_loader import clone_router
from services.user_service.integration_routes import router as user_integration_router

users_router = clone_router(legacy_users_router)
settings_router = clone_router(legacy_settings_router)
security_router = clone_router(legacy_security_router)
billing_router = clone_router(legacy_billing_router)
routers = (users_router, settings_router, security_router, billing_router, user_integration_router)

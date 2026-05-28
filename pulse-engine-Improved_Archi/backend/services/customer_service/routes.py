from channel_layer.router import router as unified_channel_router
from routers.conversations import router as legacy_conversations_router
from routers.customers import router as legacy_customers_router
from routers.meta import router as legacy_meta_router
from routers.misc import misc_router as legacy_misc_router
from routers.tickets import router as legacy_tickets_router
from routers.webhooks import router as legacy_webhooks_router
from shared.router_loader import clone_router
from services.customer_service.integration_routes import router as customer_integration_router

customers_router = clone_router(legacy_customers_router)
conversations_router = clone_router(legacy_conversations_router)
tickets_router = clone_router(legacy_tickets_router)
webhooks_router = clone_router(legacy_webhooks_router)
channels_router = clone_router(unified_channel_router)
meta_router = clone_router(legacy_meta_router)
misc_router = clone_router(legacy_misc_router)
routers = (
    customers_router,
    conversations_router,
    tickets_router,
    webhooks_router,
    channels_router,
    meta_router,
    misc_router,
    customer_integration_router,
)

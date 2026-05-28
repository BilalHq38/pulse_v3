from routers.products import router as legacy_products_router
from routers.public_products import router as public_products_router
from shared.router_loader import clone_router

products_router = clone_router(legacy_products_router)
routers = (products_router, public_products_router)

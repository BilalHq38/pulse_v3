from routers.leads import router as legacy_leads_router
from shared.router_loader import clone_router
from services.lead_service.integration_routes import router as lead_integration_router
from services.email_campaign_service.routes import router as email_campaigns_router

leads_router = clone_router(legacy_leads_router)
campaigns_router = clone_router(email_campaigns_router)
routers = (leads_router, lead_integration_router, campaigns_router)

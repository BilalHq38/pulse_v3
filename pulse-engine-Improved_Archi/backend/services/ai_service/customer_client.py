from __future__ import annotations

from shared.config import service_urls
from shared.service_client import ServiceClient

customer_service_client = ServiceClient(service_urls().customer)

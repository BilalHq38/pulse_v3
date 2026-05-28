from shared.config import service_urls
from shared.service_client import ServiceClient

auth_service_client = ServiceClient(service_urls().auth)

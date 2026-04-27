from __future__ import annotations

from fastapi import Request

from shared.auth.dependencies import extract_company_id_from_request as shared_extract_company_id_from_request

__all__ = ["extract_company_id_from_request"]


def extract_company_id_from_request(request: Request) -> str | None:
    return shared_extract_company_id_from_request(request)

from __future__ import annotations

from fastapi import APIRouter
from fastapi.routing import APIRoute


def clone_router(source: APIRouter) -> APIRouter:
    cloned = APIRouter()
    for route in source.routes:
        if not isinstance(route, APIRoute):
            continue
        cloned.add_api_route(
            route.path,
            route.endpoint,
            methods=sorted(route.methods or []),
            name=route.name,
            response_model=route.response_model,
            status_code=route.status_code,
            tags=route.tags,
            dependencies=route.dependencies,
            summary=route.summary,
            description=route.description,
            response_description=route.response_description,
            responses=route.responses,
            deprecated=route.deprecated,
            operation_id=route.operation_id,
            response_model_include=route.response_model_include,
            response_model_exclude=route.response_model_exclude,
            response_model_by_alias=route.response_model_by_alias,
            response_model_exclude_unset=route.response_model_exclude_unset,
            response_model_exclude_defaults=route.response_model_exclude_defaults,
            response_model_exclude_none=route.response_model_exclude_none,
            include_in_schema=route.include_in_schema,
            response_class=route.response_class,
            callbacks=route.callbacks,
            openapi_extra=route.openapi_extra,
        )
    return cloned

from __future__ import annotations

from fastapi import APIRouter, Depends

from services.email_service import render_platform_email_html, send_email_async
from services.db_helpers import get_current_user_flexible
from shared.events import DomainEvent, LoggingEventBus
from shared.schemas.contracts import SendEmailRequest

router = APIRouter()
event_bus = LoggingEventBus()


@router.post("/notifications/email")
async def send_transactional_email(payload: SendEmailRequest, current_user: dict = Depends(get_current_user_flexible)):
    company_id = current_user.get("company_id", "") or payload.company_id
    html_body = payload.html_body or render_platform_email_html(
        title=payload.subject,
        intro="A Pulse Engine workflow triggered this notification.",
        body_lines=[payload.body],
        accent="#0f766e",
    )
    await send_email_async(
        to_email=payload.to_email,
        subject=payload.subject,
        body=payload.body,
        html_body=html_body,
    )
    await event_bus.publish(
        DomainEvent(
            topic="notification.email.sent",
            company_id=company_id,
            payload={
                "to_email": payload.to_email,
                "subject": payload.subject,
                "actor_user_id": current_user.get("sub", ""),
            },
        )
    )
    return {"status": "queued", "channel": "email"}


@router.post("/notifications/alerts")
async def create_alert(payload: dict, current_user: dict = Depends(get_current_user_flexible)):
    company_id = current_user.get("company_id", "")
    await event_bus.publish(
        DomainEvent(
            topic="notification.alert.created",
            company_id=company_id,
            payload={
                "message": payload.get("message", ""),
                "severity": payload.get("severity", "info"),
                "actor_user_id": current_user.get("sub", ""),
            },
        )
    )
    return {"status": "accepted", "channel": "alert"}

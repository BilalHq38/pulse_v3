"""Follow-up scheduler — proactive turns after order lifecycle events."""

from services.followup_scheduler.disengagement import detect_disengagement
from services.followup_scheduler.loop import (
    FollowupSchedulerHandle,
    dispatch_one_followup,
    start_followup_scheduler,
    stop_followup_scheduler,
)
from services.followup_scheduler.replies import (
    ACK_FEEDBACK,
    ACK_OPT_OUT,
    ack_message_for,
    on_customer_reply,
)
from services.followup_scheduler.scheduler import (
    claim_due_followup,
    evaluate_order_event,
    mark_followup_outcome,
    schedule_upsell_followup,
)
from services.followup_scheduler.sentiment import classify_sentiment

__all__ = [
    "ACK_FEEDBACK",
    "ACK_OPT_OUT",
    "FollowupSchedulerHandle",
    "ack_message_for",
    "claim_due_followup",
    "classify_sentiment",
    "detect_disengagement",
    "dispatch_one_followup",
    "evaluate_order_event",
    "mark_followup_outcome",
    "on_customer_reply",
    "schedule_upsell_followup",
    "start_followup_scheduler",
    "stop_followup_scheduler",
]

BEGIN;

ALTER TABLE ai_followups
    DROP CONSTRAINT IF EXISTS chk_ai_followups_workflow_kind;

ALTER TABLE ai_followups
    ADD CONSTRAINT chk_ai_followups_workflow_kind
    CHECK (workflow_kind IN ('post_delivery_feedback','upsell','order_confirmed'));

COMMIT;

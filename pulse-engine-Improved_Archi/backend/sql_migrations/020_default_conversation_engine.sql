-- Wave 6: default-on the conversation engine.
-- The Wave 3b flag is now opt-out: new companies get the engine as their
-- response generator; existing companies are backfilled so their next
-- inbound message routes through the engine. Roll back by flipping a
-- specific company's column to FALSE; no destructive deletes.

ALTER TABLE company_settings
    ALTER COLUMN ai_use_conversation_engine SET DEFAULT TRUE;

UPDATE company_settings
   SET ai_use_conversation_engine = TRUE
 WHERE ai_use_conversation_engine IS DISTINCT FROM TRUE;

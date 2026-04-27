-- Read-only orphan check for merge history rows whose source or target unified identity profile no longer exists.
-- Note: profile_merge_history.source_customer_id and target_customer_id store unified_customers.customer_id UUIDs,
-- not Pulse CRM customers.id values, so this query intentionally joins unified_customers.

SELECT
    pmh.tenant_id,
    pmh.merge_id,
    pmh.source_customer_id,
    pmh.target_customer_id,
    pmh.merge_reason,
    pmh.merged_by,
    pmh.merged_at,
    (pmh.source_customer_id IS NOT NULL AND src.customer_id IS NULL) AS missing_source_customer,
    (pmh.target_customer_id IS NOT NULL AND tgt.customer_id IS NULL) AS missing_target_customer
FROM profile_merge_history pmh
LEFT JOIN unified_customers src
    ON src.customer_id = pmh.source_customer_id
LEFT JOIN unified_customers tgt
    ON tgt.customer_id = pmh.target_customer_id
WHERE (pmh.source_customer_id IS NOT NULL AND src.customer_id IS NULL)
   OR (pmh.target_customer_id IS NOT NULL AND tgt.customer_id IS NULL)
ORDER BY pmh.tenant_id, pmh.merged_at DESC, pmh.merge_id;

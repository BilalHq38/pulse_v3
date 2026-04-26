-- Read-only orphan check for review queue rows whose source or candidate customer no longer exists.

SELECT
    rq.tenant_id,
    rq.review_id,
    rq.resolution_id,
    rq.source_customer_id,
    rq.candidate_customer_id,
    rq.status,
    rq.created_at,
    (rq.source_customer_id IS NOT NULL AND src.customer_id IS NULL) AS missing_source_customer,
    (rq.candidate_customer_id IS NOT NULL AND cand.customer_id IS NULL) AS missing_candidate_customer
FROM review_queue rq
LEFT JOIN unified_customers src
    ON src.customer_id = rq.source_customer_id
LEFT JOIN unified_customers cand
    ON cand.customer_id = rq.candidate_customer_id
WHERE (rq.source_customer_id IS NOT NULL AND src.customer_id IS NULL)
   OR (rq.candidate_customer_id IS NOT NULL AND cand.customer_id IS NULL)
ORDER BY rq.tenant_id, rq.created_at DESC, rq.review_id;

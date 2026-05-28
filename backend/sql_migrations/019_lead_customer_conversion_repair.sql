-- Migration 019: Repair lead/customer linkage for lifecycle_stage='customer' rows.
--
-- BEFORE RUNNING: Execute the SELECT preview at the bottom to verify the match
-- set looks correct. Wrong matches on shared placeholder emails (e.g. info@company.com)
-- will not be applied because of the guard conditions below.
--
-- Guards applied:
-- 1. Email must contain '@', must not be empty, and must not start with 'info@'
--    or 'noreply@' (placeholder addresses that can match multiple records).
-- 2. Phone must have at least 7 digits after stripping non-numeric characters.
-- 3. Only matches within the same company_id.
-- 4. customer.lifecycle_stage must be 'customer'.

BEGIN;

-- Step 1: Update customers.lead_id for lifecycle_stage='customer' rows where
-- lead_id is empty or references a non-existent lead, using email or phone match.
UPDATE customers c
SET
    lead_id = l.id,
    updated_at = NOW()
FROM leads l
WHERE
    c.company_id = l.company_id
    AND c.lifecycle_stage = 'customer'
    AND (c.lead_id IS NULL OR c.lead_id = '' OR NOT EXISTS (
        SELECT 1 FROM leads ck WHERE ck.id = c.lead_id AND ck.company_id = c.company_id
    ))
    AND (
        (
            c.email IS NOT NULL AND c.email <> ''
            AND c.email NOT ILIKE 'info@%' AND c.email NOT ILIKE 'noreply@%'
            AND position('@' IN c.email) > 1
            AND LOWER(c.email) = LOWER(l.email)
        )
        OR (
            c.phone IS NOT NULL AND c.phone <> ''
            AND length(regexp_replace(c.phone, '\D', '', 'g')) >= 7
            AND regexp_replace(c.phone, '\D', '', 'g') = regexp_replace(l.phone, '\D', '', 'g')
            AND regexp_replace(l.phone, '\D', '', 'g') <> ''
            AND length(regexp_replace(l.phone, '\D', '', 'g')) >= 7
        )
    );

-- Step 2: Update linked leads to status='converted' and set lifecycle_stage metadata.
UPDATE leads l
SET
    status = 'converted',
    metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
        'lifecycle_stage', 'customer',
        'converted_at', to_char(NOW(), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    ),
    updated_at = NOW()
WHERE
    EXISTS (
        SELECT 1 FROM customers c
        WHERE c.company_id = l.company_id
          AND c.lead_id = l.id
          AND c.lifecycle_stage = 'customer'
    )
    AND (l.status IS NULL OR l.status <> 'converted');

COMMIT;

-- Preview query (run separately BEFORE applying migration to check row counts):
-- SELECT
--     l.id AS lead_id, l.email AS lead_email, l.phone AS lead_phone,
--     c.id AS customer_id, c.email AS customer_email, c.phone AS customer_phone,
--     c.lead_id AS current_lead_id
-- FROM customers c
-- JOIN leads l ON l.company_id = c.company_id
-- WHERE
--     c.lifecycle_stage = 'customer'
--     AND (c.lead_id IS NULL OR c.lead_id = '' OR NOT EXISTS (
--         SELECT 1 FROM leads ck WHERE ck.id = c.lead_id AND ck.company_id = c.company_id
--     ))
--     AND (
--         (c.email IS NOT NULL AND c.email <> '' AND c.email NOT ILIKE 'info@%'
--          AND c.email NOT ILIKE 'noreply@%' AND position('@' IN c.email) > 1
--          AND LOWER(c.email) = LOWER(l.email))
--         OR (c.phone IS NOT NULL AND c.phone <> ''
--             AND length(regexp_replace(c.phone, '\D', '', 'g')) >= 7
--             AND regexp_replace(c.phone, '\D', '', 'g') = regexp_replace(l.phone, '\D', '', 'g')
--             AND regexp_replace(l.phone, '\D', '', 'g') <> ''
--             AND length(regexp_replace(l.phone, '\D', '', 'g')) >= 7)
--     )
-- ORDER BY c.company_id, c.id;

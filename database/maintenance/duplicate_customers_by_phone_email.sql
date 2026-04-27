-- Read-only duplicate customer counts per tenant.

WITH normalized_customers AS (
    SELECT
        company_id,
        NULLIF(BTRIM(LOWER(email)), '') AS normalized_email,
        NULLIF(REGEXP_REPLACE(COALESCE(phone, ''), '\D', '', 'g'), '') AS normalized_phone
    FROM customers
)
SELECT
    company_id,
    'email' AS duplicate_type,
    normalized_email AS duplicate_value,
    COUNT(*) AS duplicate_count
FROM normalized_customers
WHERE normalized_email IS NOT NULL
GROUP BY company_id, normalized_email
HAVING COUNT(*) > 1
UNION ALL
SELECT
    company_id,
    'phone' AS duplicate_type,
    normalized_phone AS duplicate_value,
    COUNT(*) AS duplicate_count
FROM normalized_customers
WHERE normalized_phone IS NOT NULL
GROUP BY company_id, normalized_phone
HAVING COUNT(*) > 1
ORDER BY company_id, duplicate_type, duplicate_count DESC, duplicate_value;

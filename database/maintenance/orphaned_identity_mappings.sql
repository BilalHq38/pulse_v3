-- Read-only orphan check for identity mappings that no longer point at a unified customer.

SELECT
    im.tenant_id,
    im.mapping_id,
    im.customer_id,
    im.platform,
    im.platform_user_id,
    im.linked_at
FROM identity_mappings im
LEFT JOIN unified_customers uc
    ON uc.customer_id = im.customer_id
WHERE uc.customer_id IS NULL
ORDER BY im.tenant_id, im.linked_at DESC, im.mapping_id;

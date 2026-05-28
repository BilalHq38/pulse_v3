BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'customers'
          AND column_name = 'company'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'customers'
          AND column_name = 'customer_company_name'
    ) THEN
        ALTER TABLE customers RENAME COLUMN company TO customer_company_name;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'leads'
          AND column_name = 'company'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'leads'
          AND column_name = 'customer_company_name'
    ) THEN
        ALTER TABLE leads RENAME COLUMN company TO customer_company_name;
    END IF;
END $$;

COMMIT;

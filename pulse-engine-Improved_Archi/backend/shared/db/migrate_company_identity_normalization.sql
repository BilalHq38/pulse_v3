BEGIN;

-- Move any tenant identity accidentally stored in company_settings back to companies.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'company_settings'
          AND column_name = 'company_name'
    ) THEN
        UPDATE companies AS c
        SET
            name = BTRIM(cs.company_name),
            updated_at = NOW()
        FROM company_settings AS cs
        WHERE cs.company_id = c.id
          AND NULLIF(BTRIM(cs.company_name), '') IS NOT NULL
          AND (
              NULLIF(BTRIM(c.name), '') IS NULL
              OR BTRIM(c.name) = 'My Company'
          );

        ALTER TABLE company_settings
            DROP COLUMN IF EXISTS company_name;
    END IF;
END $$;

-- Keep company_settings as pure configuration metadata with a strict 1:1 tenant link.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'company_settings'::regclass
          AND contype = 'f'
          AND conkey = ARRAY[
              (
                  SELECT attnum
                  FROM pg_attribute
                  WHERE attrelid = 'company_settings'::regclass
                    AND attname = 'company_id'
                    AND NOT attisdropped
              )
          ]::smallint[]
    ) THEN
        ALTER TABLE company_settings
            ADD CONSTRAINT fk_company_settings_company_id
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'company_settings'::regclass
          AND contype = 'u'
          AND conkey = ARRAY[
              (
                  SELECT attnum
                  FROM pg_attribute
                  WHERE attrelid = 'company_settings'::regclass
                    AND attname = 'company_id'
                    AND NOT attisdropped
              )
          ]::smallint[]
    ) THEN
        ALTER TABLE company_settings
            ADD CONSTRAINT uq_company_settings_company_id
            UNIQUE (company_id);
    END IF;
END $$;

COMMIT;

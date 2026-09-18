-- Store the custom record color used by administrators to classify visits.

ALTER TABLE records
    ADD COLUMN IF NOT EXISTS custom_color VARCHAR(16) NULL AFTER comment;

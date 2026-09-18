-- Allow audited deletion of monthly marketing budgets.
-- Existing budgets and audit entries are preserved.

ALTER TABLE marketing_budget_audit
    MODIFY COLUMN action ENUM('created','updated','deleted') NOT NULL;

INSERT IGNORE INTO amo_schema_migrations (version, description)
VALUES ('004', 'Budget registry editing and audited deletion');

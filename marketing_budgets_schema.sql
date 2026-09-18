-- Monthly marketing budgets for the management dashboard.
-- Apply once after marketing_expenses_schema.sql.

CREATE TABLE IF NOT EXISTS marketing_budgets (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    source VARCHAR(64) NOT NULL,
    branch VARCHAR(255) NOT NULL,
    direction VARCHAR(64) NOT NULL,
    budget_month DATE NOT NULL,
    amount DECIMAL(14,2) NOT NULL,
    created_by VARCHAR(255) NOT NULL,
    updated_by VARCHAR(255) NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
        ON UPDATE CURRENT_TIMESTAMP(3),
    UNIQUE KEY uq_marketing_budget_scope (
        source, branch, direction, budget_month
    ),
    KEY idx_marketing_budget_month (budget_month, source, branch, direction),
    CONSTRAINT chk_marketing_budget_positive CHECK (amount > 0),
    CONSTRAINT chk_marketing_budget_first_day CHECK (DAY(budget_month) = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS marketing_budget_audit (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    budget_id BIGINT UNSIGNED NOT NULL,
    action ENUM('created','updated','deleted') NOT NULL,
    old_json LONGTEXT NULL,
    new_json LONGTEXT NOT NULL,
    changed_by VARCHAR(255) NOT NULL,
    changed_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_marketing_budget_audit (budget_id, changed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO amo_schema_migrations (version, description)
VALUES ('003', 'Monthly marketing budgets by source branch and direction');

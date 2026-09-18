-- Marketing expense ledger for the management dashboard.
-- Apply once after amocrm_schema.sql.

CREATE TABLE IF NOT EXISTS marketing_cpl_rates (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    source VARCHAR(64) NOT NULL,
    direction VARCHAR(64) NOT NULL,
    cost_per_lead DECIMAL(14,2) NOT NULL,
    valid_from DATE NOT NULL,
    valid_to DATE NULL,
    comment VARCHAR(1000) NULL,
    created_by VARCHAR(255) NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uq_marketing_cpl_rate_start (
        source, direction, valid_from
    ),
    KEY idx_marketing_cpl_rate_period (
        source, direction, valid_from, valid_to
    ),
    CONSTRAINT chk_marketing_cpl_rate_positive CHECK (cost_per_lead > 0),
    CONSTRAINT chk_marketing_cpl_rate_dates CHECK (
        valid_to IS NULL OR valid_to >= valid_from
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS marketing_period_expenses (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    source VARCHAR(64) NOT NULL,
    branch VARCHAR(255) NULL,
    direction VARCHAR(64) NULL,
    period_from DATE NOT NULL,
    period_to DATE NOT NULL,
    amount DECIMAL(14,2) NOT NULL,
    status ENUM('draft','confirmed','cancelled') NOT NULL DEFAULT 'draft',
    comment VARCHAR(1000) NULL,
    created_by VARCHAR(255) NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
        ON UPDATE CURRENT_TIMESTAMP(3),
    KEY idx_marketing_period_lookup (
        source, branch, direction, period_from, period_to, status
    ),
    KEY idx_marketing_period_dates (period_from, period_to),
    CONSTRAINT chk_marketing_period_amount_positive CHECK (amount > 0),
    CONSTRAINT chk_marketing_period_dates CHECK (period_to >= period_from)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS marketing_expense_audit (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    entity_type ENUM('cpl_rate','period_expense') NOT NULL,
    entity_id BIGINT UNSIGNED NOT NULL,
    action VARCHAR(64) NOT NULL,
    old_json LONGTEXT NULL,
    new_json LONGTEXT NULL,
    changed_by VARCHAR(255) NOT NULL,
    changed_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_marketing_expense_audit_entity (
        entity_type, entity_id, changed_at
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO amo_schema_migrations (version, description)
VALUES ('002', 'Historical marketing rates and period expenses');

-- Isolated monthly plan/fact storage. This migration is additive-only.
-- It does not alter amoCRM, Yclients, marketing analytics, or budget tables.

CREATE TABLE IF NOT EXISTS plan_fact_plans (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    plan_month DATE NOT NULL,
    branch VARCHAR(255) NOT NULL,
    direction VARCHAR(64) NOT NULL,
    formula_version VARCHAR(32) NOT NULL DEFAULT '2026-09-v1',
    created_by VARCHAR(255) NOT NULL,
    updated_by VARCHAR(255) NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
        ON UPDATE CURRENT_TIMESTAMP(3),
    UNIQUE KEY uq_plan_fact_scope (plan_month, branch, direction),
    KEY idx_plan_fact_month (plan_month, branch, direction),
    CONSTRAINT chk_plan_fact_month_first_day CHECK (DAY(plan_month) = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS plan_fact_values (
    plan_id BIGINT UNSIGNED NOT NULL,
    metric_code VARCHAR(64) NOT NULL,
    metric_value DECIMAL(20,4) NOT NULL,
    value_source ENUM('manual','calculated') NOT NULL,
    PRIMARY KEY (plan_id, metric_code),
    CONSTRAINT fk_plan_fact_values_plan
        FOREIGN KEY (plan_id) REFERENCES plan_fact_plans(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS plan_fact_plan_audit (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    plan_id BIGINT UNSIGNED NOT NULL,
    action ENUM('created','updated','deleted') NOT NULL,
    old_json LONGTEXT NULL,
    new_json LONGTEXT NOT NULL,
    changed_by VARCHAR(255) NOT NULL,
    changed_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_plan_fact_audit (plan_id, changed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS plan_fact_booking_events (
    amo_lead_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    first_booking_date DATE NOT NULL,
    source VARCHAR(128) NOT NULL,
    branch VARCHAR(255) NOT NULL,
    direction VARCHAR(64) NOT NULL,
    first_yclients_company_id BIGINT UNSIGNED NULL,
    first_yclients_record_id BIGINT UNSIGNED NULL,
    current_yclients_company_id BIGINT UNSIGNED NULL,
    current_yclients_record_id BIGINT UNSIGNED NULL,
    reconstruction_quality ENUM('exact','reconstructed') NOT NULL,
    captured_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    last_seen_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
        ON UPDATE CURRENT_TIMESTAMP(3),
    KEY idx_plan_fact_booking_month (first_booking_date, branch, direction),
    KEY idx_plan_fact_booking_record (
        current_yclients_company_id, current_yclients_record_id
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS plan_fact_data_issues (
    issue_key VARCHAR(64) NOT NULL PRIMARY KEY,
    issue_month DATE NOT NULL,
    issue_type VARCHAR(64) NOT NULL,
    severity ENUM('warning','critical') NOT NULL,
    amo_lead_id BIGINT UNSIGNED NULL,
    yclients_company_id BIGINT UNSIGNED NULL,
    yclients_record_id BIGINT UNSIGNED NULL,
    source VARCHAR(128) NULL,
    branch VARCHAR(255) NULL,
    direction VARCHAR(64) NULL,
    details VARCHAR(1000) NOT NULL,
    payload_json LONGTEXT NULL,
    first_seen_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    last_seen_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    resolved_at DATETIME(3) NULL,
    KEY idx_plan_fact_issue_month (issue_month, resolved_at, severity),
    KEY idx_plan_fact_issue_lead (amo_lead_id, resolved_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO amo_schema_migrations (version, description)
VALUES ('005', 'Isolated monthly plan-fact plans, booking events, and data quality issues');

-- Additive-only finance tables for Yclients analytics.
-- This migration does not alter or delete existing loader tables.

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS visit_service_finance (
    id                      BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    yclients_company_id     INT UNSIGNED NOT NULL,
    yclients_client_id      INT UNSIGNED NOT NULL,
    yclients_record_id      BIGINT UNSIGNED NOT NULL,
    yclients_visit_id       BIGINT UNSIGNED NULL,
    yclients_service_id     INT UNSIGNED NOT NULL,
    title                   VARCHAR(512) NULL,
    amount                  DECIMAL(12, 3) NULL,
    first_cost              DECIMAL(14, 2) NULL,
    cost_to_pay             DECIMAL(14, 2) NULL,
    paid_sum                DECIMAL(14, 2) NULL,
    payment_status          VARCHAR(32) NULL,
    paid_abonements_count   DECIMAL(12, 3) NULL,
    payed_cost              DECIMAL(14, 2) NULL,
    is_paid_full            TINYINT(1) NULL,
    discount_percent        DECIMAL(8, 3) NULL,
    attendance              TINYINT NULL,
    record_date             DATETIME NULL,
    is_deleted              TINYINT(1) NOT NULL DEFAULT 0,
    synced_at               DATETIME NOT NULL,
    UNIQUE KEY uq_visit_service_finance (
        yclients_company_id, yclients_record_id, yclients_service_id
    ),
    KEY idx_vsf_client (yclients_company_id, yclients_client_id),
    KEY idx_vsf_visit (yclients_company_id, yclients_visit_id),
    KEY idx_vsf_record_date (record_date),
    KEY idx_vsf_payment_status (payment_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS visit_goods_finance (
    id                              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    yclients_company_id             INT UNSIGNED NOT NULL,
    yclients_client_id              INT UNSIGNED NOT NULL,
    yclients_goods_transaction_id   BIGINT UNSIGNED NOT NULL,
    yclients_record_id              BIGINT UNSIGNED NULL,
    yclients_visit_id               BIGINT UNSIGNED NULL,
    yclients_good_id                INT UNSIGNED NOT NULL,
    title                           VARCHAR(512) NULL,
    amount                          DECIMAL(12, 3) NULL,
    unit                            VARCHAR(64) NULL,
    cost_per_unit                   DECIMAL(14, 2) NULL,
    first_cost                      DECIMAL(14, 2) NULL,
    cost_to_pay                     DECIMAL(14, 2) NULL,
    paid_sum                        DECIMAL(14, 2) NULL,
    payment_status                  VARCHAR(32) NULL,
    discount_percent                DECIMAL(8, 3) NULL,
    transaction_date                DATETIME NULL,
    yclients_staff_id               INT UNSIGNED NULL,
    synced_at                       DATETIME NOT NULL,
    UNIQUE KEY uq_visit_goods_finance (
        yclients_company_id, yclients_goods_transaction_id, yclients_good_id
    ),
    KEY idx_vgf_client (yclients_company_id, yclients_client_id),
    KEY idx_vgf_visit (yclients_company_id, yclients_visit_id),
    KEY idx_vgf_record (yclients_company_id, yclients_record_id),
    KEY idx_vgf_transaction_date (transaction_date),
    KEY idx_vgf_payment_status (payment_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sale_items (
    id                          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    yclients_company_id         INT UNSIGNED NOT NULL,
    source_document_id          BIGINT UNSIGNED NOT NULL,
    sale_item_id                BIGINT UNSIGNED NOT NULL,
    item_id                     BIGINT UNSIGNED NULL,
    item_type                   VARCHAR(32) NULL,
    business_type               VARCHAR(32) NOT NULL DEFAULT 'other',
    expense_title               VARCHAR(255) NULL,
    title                       VARCHAR(512) NULL,
    amount                      DECIMAL(12, 3) NULL,
    default_cost_per_unit       DECIMAL(14, 2) NULL,
    default_cost_total          DECIMAL(14, 2) NULL,
    client_discount_percent     DECIMAL(8, 3) NULL,
    cost_to_pay_total           DECIMAL(14, 2) NULL,
    yclients_service_id         INT UNSIGNED NULL,
    yclients_good_id            INT UNSIGNED NULL,
    yclients_record_id          BIGINT UNSIGNED NULL,
    yclients_visit_id           BIGINT UNSIGNED NULL,
    yclients_client_id          INT UNSIGNED NULL,
    category_id                 INT UNSIGNED NULL,
    synced_at                   DATETIME NOT NULL,
    UNIQUE KEY uq_sale_item (yclients_company_id, sale_item_id),
    KEY idx_sale_item_document (yclients_company_id, source_document_id),
    KEY idx_sale_item_client (yclients_company_id, yclients_client_id),
    KEY idx_sale_item_business_type (business_type),
    KEY idx_sale_item_record (yclients_company_id, yclients_record_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sale_payment_transactions (
    id                          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    yclients_company_id         INT UNSIGNED NOT NULL,
    yclients_transaction_id     BIGINT UNSIGNED NOT NULL,
    document_id                 BIGINT UNSIGNED NULL,
    sale_item_id                BIGINT UNSIGNED NULL,
    sale_item_type              VARCHAR(32) NULL,
    yclients_service_id         INT UNSIGNED NULL,
    expense_id                  INT UNSIGNED NULL,
    expense_title               VARCHAR(255) NULL,
    account_id                  INT UNSIGNED NULL,
    account_title               VARCHAR(255) NULL,
    amount                      DECIMAL(14, 2) NOT NULL,
    payment_date                DATETIME NULL,
    yclients_record_id          BIGINT UNSIGNED NULL,
    yclients_visit_id           BIGINT UNSIGNED NULL,
    yclients_client_id          INT UNSIGNED NULL,
    is_deleted                  TINYINT(1) NOT NULL DEFAULT 0,
    synced_at                   DATETIME NOT NULL,
    UNIQUE KEY uq_sale_payment (yclients_company_id, yclients_transaction_id),
    KEY idx_sale_payment_document (yclients_company_id, document_id),
    KEY idx_sale_payment_item (yclients_company_id, sale_item_id),
    KEY idx_sale_payment_client (yclients_company_id, yclients_client_id),
    KEY idx_sale_payment_date (payment_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sale_loyalty_transactions (
    id                              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    yclients_company_id             INT UNSIGNED NOT NULL,
    yclients_loyalty_transaction_id BIGINT UNSIGNED NOT NULL,
    document_id                     BIGINT UNSIGNED NULL,
    sale_item_id                    BIGINT UNSIGNED NULL,
    sale_item_type                  VARCHAR(32) NULL,
    amount                          DECIMAL(14, 2) NOT NULL,
    type_id                         INT UNSIGNED NULL,
    type_title                      VARCHAR(255) NULL,
    created_at                      DATETIME NULL,
    synced_at                       DATETIME NOT NULL,
    UNIQUE KEY uq_sale_loyalty (yclients_company_id, yclients_loyalty_transaction_id),
    KEY idx_sale_loyalty_document (yclients_company_id, document_id),
    KEY idx_sale_loyalty_item (yclients_company_id, sale_item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS finance_client_sync_state (
    yclients_company_id     INT UNSIGNED NOT NULL,
    yclients_client_id      INT UNSIGNED NOT NULL,
    last_incremental_at     DATETIME NULL,
    full_history_synced_at  DATETIME NULL,
    last_error              TEXT NULL,
    updated_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (yclients_company_id, yclients_client_id),
    KEY idx_finance_client_incremental (last_incremental_at),
    KEY idx_finance_client_full (full_history_synced_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS finance_document_sync_state (
    yclients_company_id     INT UNSIGNED NOT NULL,
    document_id             BIGINT UNSIGNED NOT NULL,
    synced_at               DATETIME NULL,
    status                  VARCHAR(16) NOT NULL DEFAULT 'pending',
    last_error              TEXT NULL,
    updated_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (yclients_company_id, document_id),
    KEY idx_finance_document_status (status, synced_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS finance_sync_runs (
    id                  BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    mode                VARCHAR(16) NOT NULL,
    started_at          DATETIME NOT NULL,
    finished_at         DATETIME NULL,
    status              VARCHAR(16) NOT NULL DEFAULT 'running',
    clients_processed   INT UNSIGNED NOT NULL DEFAULT 0,
    documents_processed INT UNSIGNED NOT NULL DEFAULT 0,
    errors_count        INT UNSIGNED NOT NULL DEFAULT 0,
    message             TEXT NULL,
    KEY idx_finance_runs_started (started_at),
    KEY idx_finance_runs_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

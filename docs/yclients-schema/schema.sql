-- YClients → MySQL schema
-- Run once: mysql -h HOST -u USER -p DATABASE < sql/schema.sql

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE TABLE IF NOT EXISTS branches (
    id                  INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    yclients_company_id INT UNSIGNED NOT NULL,
    name                VARCHAR(255) NULL,
    is_active           TINYINT(1) NOT NULL DEFAULT 1,
    last_sync_at        DATETIME NULL,
    last_sync_status    ENUM('success', 'partial', 'failed') NULL,
    last_error          TEXT NULL,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_yclients_company (yclients_company_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sync_state (
    id                  INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    branch_id           INT UNSIGNED NOT NULL,
    entity_type         ENUM('clients', 'records') NOT NULL,
    last_changed_after  DATETIME NULL,
    first_sync_done     TINYINT(1) NOT NULL DEFAULT 0,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_branch_entity (branch_id, entity_type),
    CONSTRAINT fk_sync_state_branch FOREIGN KEY (branch_id) REFERENCES branches(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS companies (
    yclients_company_id INT UNSIGNED PRIMARY KEY,
    title               VARCHAR(255) NOT NULL,
    address             VARCHAR(512) NULL,
    city                VARCHAR(128) NULL,
    country             VARCHAR(128) NULL,
    phone               VARCHAR(64) NULL,
    timezone_name       VARCHAR(64) NULL,
    coordinate_lat      DECIMAL(10, 7) NULL,
    coordinate_lon      DECIMAL(10, 7) NULL,
    is_active           TINYINT(1) NULL,
    synced_at           DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS staff (
    id                  INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    yclients_company_id INT UNSIGNED NOT NULL,
    yclients_staff_id   INT UNSIGNED NOT NULL,
    name                VARCHAR(255) NOT NULL,
    specialization      VARCHAR(255) NULL,
    position_id         INT UNSIGNED NULL,
    position_title      VARCHAR(255) NULL,
    is_fired            TINYINT(1) NOT NULL DEFAULT 0,
    is_hidden           TINYINT(1) NOT NULL DEFAULT 0,
    rating              DECIMAL(4, 2) NULL,
    avatar              VARCHAR(512) NULL,
    synced_at           DATETIME NOT NULL,
    UNIQUE KEY uq_staff (yclients_company_id, yclients_staff_id),
    KEY idx_staff_company (yclients_company_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS service_categories (
    id                      INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    yclients_company_id     INT UNSIGNED NOT NULL,
    yclients_category_id    INT UNSIGNED NOT NULL,
    title                   VARCHAR(255) NOT NULL,
    weight                  INT NULL,
    synced_at               DATETIME NOT NULL,
    UNIQUE KEY uq_category (yclients_company_id, yclients_category_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS services (
    id                  INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    yclients_company_id INT UNSIGNED NOT NULL,
    yclients_service_id INT UNSIGNED NOT NULL,
    yclients_category_id INT UNSIGNED NULL,
    title               VARCHAR(255) NOT NULL,
    price_min           DECIMAL(12, 2) NULL,
    price_max           DECIMAL(12, 2) NULL,
    duration_sec        INT NULL,
    is_active           TINYINT(1) NULL,
    synced_at           DATETIME NOT NULL,
    UNIQUE KEY uq_service (yclients_company_id, yclients_service_id),
    KEY idx_service_category (yclients_company_id, yclients_category_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS clients (
    id                  INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    yclients_company_id INT UNSIGNED NOT NULL,
    yclients_client_id  INT UNSIGNED NOT NULL,
    name                VARCHAR(255) NULL,
    phone               VARCHAR(32) NULL,
    email               VARCHAR(255) NULL,
    discount            DECIMAL(8, 2) NULL,
    visits_count        INT NULL,
    first_visit_date    DATETIME NULL,
    last_visit_date     DATETIME NULL,
    sold_amount         DECIMAL(12, 2) NULL,
    is_deleted          TINYINT(1) NOT NULL DEFAULT 0,
    last_change_date    DATETIME NULL,
    synced_at           DATETIME NOT NULL,
    UNIQUE KEY uq_client (yclients_company_id, yclients_client_id),
    KEY idx_client_phone (phone)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS records (
    id                  INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    yclients_company_id INT UNSIGNED NOT NULL,
    yclients_record_id  INT UNSIGNED NOT NULL,
    yclients_client_id  INT UNSIGNED NULL,
    yclients_staff_id   INT UNSIGNED NULL,
    datetime            DATETIME NOT NULL,
    create_date         DATETIME NULL,
    comment             TEXT NULL,
    custom_color        VARCHAR(16) NULL,
    attendance          TINYINT NULL,
    visit_attendance    TINYINT NULL,
    confirmed           TINYINT(1) NULL,
    is_online           TINYINT(1) NULL,
    is_deleted          TINYINT(1) NOT NULL DEFAULT 0,
    status              VARCHAR(32) NOT NULL DEFAULT 'active',
    paid_full           TINYINT(1) NULL,
    prepaid             TINYINT(1) NULL,
    seance_length_sec   INT NULL,
    last_change_date    DATETIME NULL,
    synced_at           DATETIME NOT NULL,
    UNIQUE KEY uq_record (yclients_company_id, yclients_record_id),
    KEY idx_record_datetime (datetime),
    KEY idx_record_client (yclients_company_id, yclients_client_id),
    KEY idx_record_staff (yclients_company_id, yclients_staff_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS record_services (
    id                  INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    yclients_company_id INT UNSIGNED NOT NULL,
    yclients_record_id  INT UNSIGNED NOT NULL,
    yclients_service_id INT UNSIGNED NOT NULL,
    title               VARCHAR(255) NULL,
    cost                DECIMAL(12, 2) NULL,
    amount              INT NULL DEFAULT 1,
    synced_at           DATETIME NOT NULL,
    UNIQUE KEY uq_record_service (yclients_company_id, yclients_record_id, yclients_service_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sync_runs (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    sync_type       ENUM('reference', 'clients', 'records', 'full') NULL,
    started_at      DATETIME NOT NULL,
    finished_at     DATETIME NULL,
    status          ENUM('running', 'success', 'partial', 'failed') NOT NULL,
    branches_ok     INT UNSIGNED DEFAULT 0,
    branches_failed INT UNSIGNED DEFAULT 0,
    message         TEXT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sync_logs (
    id                  BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    sync_run_id         INT UNSIGNED NULL,
    sync_type           ENUM('reference', 'clients', 'records', 'full') NULL,
    branch_id           INT UNSIGNED NULL,
    yclients_company_id INT UNSIGNED NULL,
    branch_name         VARCHAR(255) NULL,
    started_at          DATETIME NOT NULL,
    finished_at         DATETIME NULL,
    status              ENUM('running', 'success', 'partial', 'failed') NOT NULL DEFAULT 'running',
    error_message       TEXT NULL COMMENT 'Текст ошибки; NULL если синхронизация успешна',
    details             TEXT NULL COMMENT 'Сводка: кол-во загруженных сущностей',
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_sync_logs_started (started_at),
    KEY idx_sync_logs_branch (branch_id),
    KEY idx_sync_logs_company (yclients_company_id),
    KEY idx_sync_logs_run (sync_run_id),
    CONSTRAINT fk_sync_logs_branch FOREIGN KEY (branch_id) REFERENCES branches(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;

-- Initial branch (YClients company_id)
INSERT INTO branches (yclients_company_id, is_active)
VALUES (794828, 1)
ON DUPLICATE KEY UPDATE is_active = VALUES(is_active);

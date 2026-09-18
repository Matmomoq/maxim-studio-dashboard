-- Migration: add sync_logs table (journal of each branch sync)
CREATE TABLE IF NOT EXISTS sync_logs (
    id                  BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    sync_run_id         INT UNSIGNED NULL,
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

-- amoCRM warehouse schema for MariaDB 10.11 / MySQL-compatible servers.
-- Timestamps are stored in UTC and converted to Europe/Moscow in analytics.

CREATE TABLE IF NOT EXISTS amo_schema_migrations (
    version VARCHAR(32) NOT NULL PRIMARY KEY,
    description VARCHAR(255) NOT NULL,
    applied_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_oauth_tokens (
    id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
    account_id BIGINT UNSIGNED NULL,
    account_subdomain VARCHAR(255) NOT NULL,
    access_token_encrypted LONGBLOB NOT NULL,
    refresh_token_encrypted LONGBLOB NOT NULL,
    encryption_salt BINARY(16) NOT NULL,
    access_nonce BINARY(12) NOT NULL,
    refresh_nonce BINARY(12) NOT NULL,
    token_type VARCHAR(32) NOT NULL DEFAULT 'Bearer',
    expires_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_accounts (
    amo_account_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    account_name VARCHAR(255) NULL,
    subdomain VARCHAR(255) NOT NULL,
    country_code CHAR(2) NULL,
    currency_code CHAR(3) NULL,
    timezone_name VARCHAR(64) NULL,
    created_at DATETIME(3) NULL,
    updated_at DATETIME(3) NULL,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    raw_json LONGTEXT NULL,
    UNIQUE KEY uq_amo_accounts_subdomain (subdomain)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_users (
    amo_user_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    user_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NULL,
    language_code VARCHAR(16) NULL,
    group_id BIGINT UNSIGNED NULL,
    role_id BIGINT UNSIGNED NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    is_admin TINYINT(1) NOT NULL DEFAULT 0,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    raw_json LONGTEXT NULL,
    KEY idx_amo_users_group (group_id),
    KEY idx_amo_users_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_pipelines (
    amo_pipeline_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    pipeline_name VARCHAR(255) NOT NULL,
    sort_order INT NULL,
    is_main TINYINT(1) NOT NULL DEFAULT 0,
    is_unsorted_on TINYINT(1) NOT NULL DEFAULT 0,
    is_archive TINYINT(1) NOT NULL DEFAULT 0,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    raw_json LONGTEXT NULL,
    KEY idx_amo_pipelines_archive (is_archive)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_statuses (
    amo_pipeline_id BIGINT UNSIGNED NOT NULL,
    amo_status_id BIGINT UNSIGNED NOT NULL,
    status_name VARCHAR(255) NOT NULL,
    sort_order INT NULL,
    status_type INT NULL,
    is_final_success TINYINT(1) NOT NULL DEFAULT 0,
    is_final_failure TINYINT(1) NOT NULL DEFAULT 0,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    raw_json LONGTEXT NULL,
    PRIMARY KEY (amo_pipeline_id, amo_status_id),
    KEY idx_amo_statuses_status (amo_status_id),
    KEY idx_amo_statuses_sort (amo_pipeline_id, sort_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_custom_fields (
    entity_type VARCHAR(32) NOT NULL,
    amo_field_id BIGINT UNSIGNED NOT NULL,
    field_name VARCHAR(255) NOT NULL,
    field_code VARCHAR(128) NULL,
    field_type VARCHAR(64) NOT NULL,
    group_id BIGINT UNSIGNED NULL,
    sort_order INT NULL,
    is_api_only TINYINT(1) NOT NULL DEFAULT 0,
    is_required TINYINT(1) NOT NULL DEFAULT 0,
    is_deletable TINYINT(1) NULL,
    is_visible TINYINT(1) NULL,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    raw_json LONGTEXT NULL,
    PRIMARY KEY (entity_type, amo_field_id),
    KEY idx_amo_custom_fields_code (entity_type, field_code),
    KEY idx_amo_custom_fields_name (entity_type, field_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_tags (
    amo_tag_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    tag_name VARCHAR(255) NOT NULL,
    color VARCHAR(32) NULL,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    raw_json LONGTEXT NULL,
    KEY idx_amo_tags_name (tag_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_leads (
    amo_lead_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    lead_name VARCHAR(255) NOT NULL,
    price DECIMAL(18,2) NOT NULL DEFAULT 0,
    amo_pipeline_id BIGINT UNSIGNED NOT NULL,
    amo_status_id BIGINT UNSIGNED NOT NULL,
    responsible_user_id BIGINT UNSIGNED NULL,
    group_id BIGINT UNSIGNED NULL,
    created_by BIGINT UNSIGNED NULL,
    updated_by BIGINT UNSIGNED NULL,
    created_at DATETIME(3) NULL,
    updated_at DATETIME(3) NULL,
    closed_at DATETIME(3) NULL,
    closest_task_at DATETIME(3) NULL,
    is_deleted TINYINT(1) NOT NULL DEFAULT 0,
    yclients_record_id BIGINT UNSIGNED NULL,
    yclients_company_id BIGINT UNSIGNED NULL,
    appointment_date DATE NULL,
    appointment_time VARCHAR(32) NULL,
    branch_name VARCHAR(255) NULL,
    visit_status VARCHAR(128) NULL,
    services_text LONGTEXT NULL,
    appointment_transition_date DATE NULL,
    appointment_source VARCHAR(255) NULL,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    raw_json LONGTEXT NULL,
    KEY idx_amo_leads_pipeline_status (amo_pipeline_id, amo_status_id),
    KEY idx_amo_leads_responsible (responsible_user_id),
    KEY idx_amo_leads_created (created_at),
    KEY idx_amo_leads_updated (updated_at),
    KEY idx_amo_leads_yclients_record (yclients_company_id, yclients_record_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_contacts (
    amo_contact_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    contact_name VARCHAR(255) NULL,
    first_name VARCHAR(255) NULL,
    last_name VARCHAR(255) NULL,
    responsible_user_id BIGINT UNSIGNED NULL,
    group_id BIGINT UNSIGNED NULL,
    created_by BIGINT UNSIGNED NULL,
    updated_by BIGINT UNSIGNED NULL,
    created_at DATETIME(3) NULL,
    updated_at DATETIME(3) NULL,
    closest_task_at DATETIME(3) NULL,
    yclients_client_id BIGINT UNSIGNED NULL,
    client_category VARCHAR(255) NULL,
    network_visits_count INT NULL,
    subscription_name VARCHAR(255) NULL,
    subscription_start_date DATE NULL,
    subscription_end_date DATE NULL,
    is_deleted TINYINT(1) NOT NULL DEFAULT 0,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    raw_json LONGTEXT NULL,
    KEY idx_amo_contacts_responsible (responsible_user_id),
    KEY idx_amo_contacts_created (created_at),
    KEY idx_amo_contacts_updated (updated_at),
    KEY idx_amo_contacts_yclients_client (yclients_client_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_companies (
    amo_company_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    company_name VARCHAR(255) NULL,
    responsible_user_id BIGINT UNSIGNED NULL,
    group_id BIGINT UNSIGNED NULL,
    created_by BIGINT UNSIGNED NULL,
    updated_by BIGINT UNSIGNED NULL,
    created_at DATETIME(3) NULL,
    updated_at DATETIME(3) NULL,
    closest_task_at DATETIME(3) NULL,
    is_deleted TINYINT(1) NOT NULL DEFAULT 0,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    raw_json LONGTEXT NULL,
    KEY idx_amo_companies_responsible (responsible_user_id),
    KEY idx_amo_companies_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_contact_phones (
    amo_contact_id BIGINT UNSIGNED NOT NULL,
    ordinal_position SMALLINT UNSIGNED NOT NULL,
    phone_original VARCHAR(64) NOT NULL,
    phone_normalized VARCHAR(32) NULL,
    phone_type VARCHAR(64) NULL,
    enum_id BIGINT UNSIGNED NULL,
    is_primary TINYINT(1) NOT NULL DEFAULT 0,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (amo_contact_id, ordinal_position),
    KEY idx_amo_contact_phones_normalized (phone_normalized),
    KEY idx_amo_contact_phones_contact_normalized (amo_contact_id, phone_normalized)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_contact_emails (
    amo_contact_id BIGINT UNSIGNED NOT NULL,
    ordinal_position SMALLINT UNSIGNED NOT NULL,
    email VARCHAR(320) NOT NULL,
    email_normalized VARCHAR(320) NULL,
    email_type VARCHAR(64) NULL,
    enum_id BIGINT UNSIGNED NULL,
    is_primary TINYINT(1) NOT NULL DEFAULT 0,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (amo_contact_id, ordinal_position),
    KEY idx_amo_contact_emails_normalized (email_normalized(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_lead_contacts (
    amo_lead_id BIGINT UNSIGNED NOT NULL,
    amo_contact_id BIGINT UNSIGNED NOT NULL,
    is_main_contact TINYINT(1) NOT NULL DEFAULT 0,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (amo_lead_id, amo_contact_id),
    KEY idx_amo_lead_contacts_contact (amo_contact_id, amo_lead_id),
    KEY idx_amo_lead_contacts_main (amo_lead_id, is_main_contact)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_custom_field_values (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    entity_type VARCHAR(32) NOT NULL,
    amo_entity_id BIGINT UNSIGNED NOT NULL,
    amo_field_id BIGINT UNSIGNED NOT NULL,
    ordinal_position SMALLINT UNSIGNED NOT NULL DEFAULT 1,
    value_text LONGTEXT NULL,
    value_number DECIMAL(24,6) NULL,
    value_datetime DATETIME(3) NULL,
    enum_id BIGINT UNSIGNED NULL,
    enum_code VARCHAR(128) NULL,
    raw_json LONGTEXT NULL,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uq_amo_custom_field_value (
        entity_type, amo_entity_id, amo_field_id, ordinal_position
    ),
    KEY idx_amo_custom_field_lookup (entity_type, amo_field_id, amo_entity_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_lead_tags (
    amo_lead_id BIGINT UNSIGNED NOT NULL,
    amo_tag_id BIGINT UNSIGNED NOT NULL,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (amo_lead_id, amo_tag_id),
    KEY idx_amo_lead_tags_tag (amo_tag_id, amo_lead_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_lead_tag_history (
    event_id VARCHAR(64) NOT NULL,
    amo_lead_id BIGINT UNSIGNED NOT NULL,
    amo_tag_id BIGINT UNSIGNED NOT NULL,
    action ENUM('added','removed') NOT NULL,
    changed_at DATETIME(3) NOT NULL,
    changed_by BIGINT UNSIGNED NULL,
    raw_json LONGTEXT NULL,
    PRIMARY KEY (event_id, amo_tag_id, action),
    KEY idx_amo_tag_history_lead_time (amo_lead_id, changed_at),
    KEY idx_amo_tag_history_tag_time (amo_tag_id, changed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_events (
    event_id VARCHAR(64) NOT NULL PRIMARY KEY,
    event_type VARCHAR(128) NOT NULL,
    entity_type VARCHAR(64) NULL,
    amo_entity_id BIGINT UNSIGNED NULL,
    created_by BIGINT UNSIGNED NULL,
    occurred_at DATETIME(3) NOT NULL,
    value_before_json LONGTEXT NULL,
    value_after_json LONGTEXT NULL,
    raw_json LONGTEXT NULL,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_amo_events_occurred (occurred_at),
    KEY idx_amo_events_type_time (event_type, occurred_at),
    KEY idx_amo_events_entity_time (entity_type, amo_entity_id, occurred_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_lead_status_history (
    event_id VARCHAR(64) NOT NULL PRIMARY KEY,
    amo_lead_id BIGINT UNSIGNED NOT NULL,
    previous_pipeline_id BIGINT UNSIGNED NULL,
    previous_status_id BIGINT UNSIGNED NULL,
    new_pipeline_id BIGINT UNSIGNED NULL,
    new_status_id BIGINT UNSIGNED NOT NULL,
    changed_at DATETIME(3) NOT NULL,
    changed_by BIGINT UNSIGNED NULL,
    stage_entry_number INT UNSIGNED NULL,
    previous_stage_duration_sec BIGINT UNSIGNED NULL,
    raw_json LONGTEXT NULL,
    KEY idx_amo_status_history_lead_time (amo_lead_id, changed_at),
    KEY idx_amo_status_history_new_stage (new_pipeline_id, new_status_id, changed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_tasks (
    amo_task_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    entity_type VARCHAR(32) NULL,
    amo_entity_id BIGINT UNSIGNED NULL,
    responsible_user_id BIGINT UNSIGNED NULL,
    created_by BIGINT UNSIGNED NULL,
    updated_by BIGINT UNSIGNED NULL,
    task_type_id BIGINT UNSIGNED NULL,
    task_text LONGTEXT NULL,
    result_text LONGTEXT NULL,
    is_completed TINYINT(1) NOT NULL DEFAULT 0,
    complete_till DATETIME(3) NULL,
    created_at DATETIME(3) NULL,
    updated_at DATETIME(3) NULL,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    raw_json LONGTEXT NULL,
    KEY idx_amo_tasks_entity (entity_type, amo_entity_id),
    KEY idx_amo_tasks_responsible_due (responsible_user_id, is_completed, complete_till),
    KEY idx_amo_tasks_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_notes (
    amo_note_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    entity_type VARCHAR(32) NOT NULL,
    amo_entity_id BIGINT UNSIGNED NOT NULL,
    note_type VARCHAR(64) NOT NULL,
    created_by BIGINT UNSIGNED NULL,
    updated_by BIGINT UNSIGNED NULL,
    responsible_user_id BIGINT UNSIGNED NULL,
    group_id BIGINT UNSIGNED NULL,
    created_at DATETIME(3) NULL,
    updated_at DATETIME(3) NULL,
    params_json LONGTEXT NULL,
    raw_json LONGTEXT NULL,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_amo_notes_entity_time (entity_type, amo_entity_id, created_at),
    KEY idx_amo_notes_type_time (note_type, created_at),
    KEY idx_amo_notes_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_calls (
    amo_note_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    event_id VARCHAR(64) NULL,
    source_entity_type VARCHAR(32) NOT NULL,
    source_entity_id BIGINT UNSIGNED NOT NULL,
    amo_contact_id BIGINT UNSIGNED NULL,
    amo_lead_id BIGINT UNSIGNED NULL,
    occurred_at DATETIME(3) NOT NULL,
    direction ENUM('incoming','outgoing') NOT NULL,
    responsible_user_id BIGINT UNSIGNED NULL,
    phone_original VARCHAR(64) NULL,
    phone_normalized VARCHAR(32) NULL,
    duration_sec INT UNSIGNED NOT NULL DEFAULT 0,
    provider VARCHAR(128) NULL,
    raw_call_status VARCHAR(64) NULL,
    raw_call_result VARCHAR(255) NULL,
    normalized_status ENUM('accepted','missed') NOT NULL,
    source_updated_at DATETIME(3) NULL,
    raw_json LONGTEXT NULL,
    synced_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uq_amo_calls_event (event_id),
    KEY idx_amo_calls_occurred (occurred_at),
    KEY idx_amo_calls_status_time (normalized_status, occurred_at),
    KEY idx_amo_calls_contact_time (amo_contact_id, occurred_at),
    KEY idx_amo_calls_lead_time (amo_lead_id, occurred_at),
    KEY idx_amo_calls_responsible_time (responsible_user_id, occurred_at),
    KEY idx_amo_calls_phone (phone_normalized)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_call_leads (
    amo_note_id BIGINT UNSIGNED NOT NULL,
    amo_lead_id BIGINT UNSIGNED NOT NULL,
    relation_type VARCHAR(64) NOT NULL,
    is_primary_relation TINYINT(1) NOT NULL DEFAULT 0,
    linked_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (amo_note_id, amo_lead_id),
    KEY idx_amo_call_leads_lead (amo_lead_id, amo_note_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_yclients_identity_map (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    amo_lead_id BIGINT UNSIGNED NULL,
    amo_contact_id BIGINT UNSIGNED NULL,
    yclients_company_id BIGINT UNSIGNED NULL,
    yclients_record_id BIGINT UNSIGNED NULL,
    yclients_client_id BIGINT UNSIGNED NULL,
    match_method VARCHAR(64) NOT NULL,
    match_confidence DECIMAL(5,4) NULL,
    matched_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    KEY idx_amo_yclients_map_lead (amo_lead_id),
    KEY idx_amo_yclients_map_contact (amo_contact_id),
    KEY idx_amo_yclients_map_record (yclients_company_id, yclients_record_id),
    KEY idx_amo_yclients_map_client (yclients_company_id, yclients_client_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_sync_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    sync_type VARCHAR(64) NOT NULL,
    started_at DATETIME(3) NOT NULL,
    finished_at DATETIME(3) NULL,
    status ENUM('running','success','partial','failed') NOT NULL,
    records_read BIGINT UNSIGNED NOT NULL DEFAULT 0,
    records_inserted BIGINT UNSIGNED NOT NULL DEFAULT 0,
    records_updated BIGINT UNSIGNED NOT NULL DEFAULT 0,
    records_failed BIGINT UNSIGNED NOT NULL DEFAULT 0,
    message LONGTEXT NULL,
    KEY idx_amo_sync_runs_type_started (sync_type, started_at),
    KEY idx_amo_sync_runs_status_started (status, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_sync_state (
    entity_type VARCHAR(64) NOT NULL PRIMARY KEY,
    cursor_updated_at DATETIME(3) NULL,
    cursor_id VARCHAR(128) NULL,
    first_sync_done TINYINT(1) NOT NULL DEFAULT 0,
    last_success_at DATETIME(3) NULL,
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
        ON UPDATE CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_sync_errors (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    sync_run_id BIGINT UNSIGNED NULL,
    entity_type VARCHAR(64) NULL,
    entity_id VARCHAR(128) NULL,
    error_code VARCHAR(64) NULL,
    error_message LONGTEXT NOT NULL,
    retryable TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_amo_sync_errors_run (sync_run_id),
    KEY idx_amo_sync_errors_entity (entity_type, entity_id),
    KEY idx_amo_sync_errors_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS amo_raw_entities (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    entity_type VARCHAR(64) NOT NULL,
    entity_id VARCHAR(128) NOT NULL,
    source_updated_at DATETIME(3) NULL,
    payload_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    payload_json LONGTEXT NOT NULL,
    fetched_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uq_amo_raw_entity_version (
        entity_type, entity_id, payload_sha256
    ),
    KEY idx_amo_raw_entities_source_time (entity_type, source_updated_at),
    KEY idx_amo_raw_entities_fetched (fetched_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
VALUES ('001', 'Initial amoCRM warehouse schema');

INSERT IGNORE INTO amo_schema_migrations (version, description)
VALUES ('002', 'Historical marketing rates and period expenses');

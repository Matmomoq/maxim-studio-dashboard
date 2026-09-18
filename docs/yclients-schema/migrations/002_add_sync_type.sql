-- Migration: sync_type column for split cron scripts
ALTER TABLE sync_runs
    ADD COLUMN sync_type ENUM('reference', 'clients', 'records', 'full') NULL AFTER id;

ALTER TABLE sync_logs
    ADD COLUMN sync_type ENUM('reference', 'clients', 'records', 'full') NULL AFTER sync_run_id;

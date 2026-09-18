-- Additive fields returned by the Yclients visit-history endpoint.

ALTER TABLE visit_service_finance
    ADD COLUMN IF NOT EXISTS paid_abonements_count DECIMAL(12, 3) NULL AFTER payment_status,
    ADD COLUMN IF NOT EXISTS payed_cost DECIMAL(14, 2) NULL AFTER paid_abonements_count,
    ADD COLUMN IF NOT EXISTS is_paid_full TINYINT(1) NULL AFTER payed_cost;


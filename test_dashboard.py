from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from dashboard.analytics import (
    EXCLUDED_LOSS_REASONS,
    DashboardAnalytics,
    LeadRecord,
    MarketingTagRecord,
    VerificationRecord,
    build_marketing_reconciliation,
)
from dashboard.app import create_app
from dashboard.call_analytics import CallAnalytics, USER_RULES_BY_ID
from dashboard.call_recordings_demo import RecordingUnavailable, validate_recording_url
from dashboard.classifier import TagClassifier
from dashboard.classifier import (
    SPEED_DIAL_LASER_SOURCE,
    SPEED_DIAL_MASSAGE_SOURCE,
    SPEED_DIAL_SOURCE,
    SPEED_DIAL_UNKNOWN_SOURCE,
    analytics_source_label,
)
from dashboard.marketing_expenses import (
    PeriodExpenseInput,
    calculate_period_preview,
    validate_period_input,
    validate_rate_input,
)
from dashboard.marketing_analytics import calculate_marketing_performance
from dashboard.budget_planning import (
    calculate_budget_rows,
    validate_budget_input,
)
from dashboard.trend_analytics import calculate_trend_data
from dashboard.funnel_analytics import (
    LeadOutcome,
    calculate_funnel_report,
    record_counts_as_visit,
)
from dashboard.yclients_analytics import (
    build_color_reconciliation,
    build_installment_debt_report,
    build_yclients_report,
    classify_direction,
    classify_visit_type,
    color_reconciliation_to_csv,
    normalize_branch,
)
from dashboard.yclients_rules import (
    administrator_prepayment_direction,
    is_administrator_staff,
)
from dashboard.plan_fact import (
    PlanFactAnalytics,
    aggregate_metric_values,
    calculate_plan_values,
    derive_actual_metrics,
    parse_plan_month,
)


class ClassifierTests(unittest.TestCase):
    def test_known_tags_and_branch_field(self):
        result = TagClassifier().classify(
            ["ВК", "массаж", "стройность1800"],
            branch_name="XS СПб Академическая",
        )
        self.assertEqual(result["branch"], "Академическая")
        self.assertEqual(result["direction"], "Массаж")
        self.assertEqual(result["source"], "VK")
        self.assertEqual(result["offer"], "стройность1800")

    def test_marketing_classification_can_ignore_branch_field(self):
        classifier = TagClassifier()
        management = classifier.classify(
            ["ВК", "массаж", "Невский"],
            branch_name="Свиблово",
        )
        marketing = classifier.classify(["ВК", "массаж", "Невский"])
        self.assertEqual(management["branch"], "Свиблово")
        self.assertEqual(marketing["branch"], "Невский")

    def test_unknown_numeric_tags_are_not_guessed_as_offers(self):
        result = TagClassifier().classify(
            ["1800", "Рис2900", "новый оффер 2900"]
        )
        self.assertEqual(result["offer"], "Не определено")
        self.assertEqual(result["source"], "Не определено")

    def test_yandex_maps_tag_is_a_source(self):
        result = TagClassifier().classify(
            ["Яндекс карты", "массаж", "Свиблово"]
        )
        self.assertEqual(result["source"], "Яндекс.Карты")
        self.assertEqual(result["direction"], "Массаж")
        self.assertEqual(result["branch"], "Свиблово")

    def test_site_tags_are_separate_sources(self):
        self.assertEqual(
            TagClassifier().classify(["сайт"])["source"],
            "Сайт Xsize",
        )
        self.assertEqual(
            TagClassifier().classify(["Сайт МСК"])["source"],
            "Сайт МСК",
        )

    def test_ris_and_ris_xs_are_separate_sources(self):
        classifier = TagClassifier()
        self.assertEqual(classifier.classify(["РИС"])["source"], "РИС")
        self.assertEqual(classifier.classify(["РИС_xs"])["source"], "РИС_xs")
        self.assertEqual(classifier.classify(["рис_хс"])["source"], "РИС_xs")

    def test_future_smm_xs_tag_is_configured(self):
        self.assertEqual(
            TagClassifier().classify(["SMM_xs"])["source"],
            "SMM_xs",
        )

    def test_speed_dial_is_split_by_direction_for_analytics(self):
        classifier = TagClassifier()
        massage = classifier.classify(["Скорозвон", "Массаж", "VK"])
        laser = classifier.classify(["скорозвон", "Лазер"])
        unknown = classifier.classify(["Скорозвон"])

        self.assertEqual(massage["source"], SPEED_DIAL_SOURCE)
        self.assertEqual(
            analytics_source_label(massage["source"], massage["direction"]),
            SPEED_DIAL_MASSAGE_SOURCE,
        )
        self.assertEqual(
            analytics_source_label(laser["source"], laser["direction"]),
            SPEED_DIAL_LASER_SOURCE,
        )
        self.assertEqual(
            analytics_source_label(unknown["source"], unknown["direction"]),
            SPEED_DIAL_UNKNOWN_SOURCE,
        )

    def test_speed_dial_with_conflicting_directions_is_not_guessed(self):
        result = TagClassifier().classify(["Скорозвон", "Массаж", "Лазер"])
        self.assertEqual(result["source"], SPEED_DIAL_SOURCE)
        self.assertEqual(result["direction"], "Не определено")


class CallRecordingDemoTests(unittest.TestCase):
    def test_only_https_comagic_recording_urls_are_allowed(self):
        self.assertEqual(
            validate_recording_url("https://media.comagic.ru/records/example.mp3"),
            "https://media.comagic.ru/records/example.mp3",
        )
        for unsafe in (
            "http://media.comagic.ru/records/example.mp3",
            "https://example.com/records/example.mp3",
            "https://media.comagic.ru.evil.example/record.mp3",
            "",
        ):
            with self.assertRaises(RecordingUnavailable):
                validate_recording_url(unsafe)


class CallAnalyticsUserRulesTests(unittest.TestCase):
    def test_maps_call_teams_by_stable_amo_user_id(self):
        rows = [
            {"amo_user_id": 13984270, "user_name": "Новое имя КЦ Москва"},
            {"amo_user_id": 13984330, "user_name": "Колл-центр СПб 1"},
            {"amo_user_id": 14114098, "user_name": "Колл-центр СПБ 2"},
            {"amo_user_id": 13984302, "user_name": "Администратор Москва"},
            {"amo_user_id": 13984342, "user_name": "Администратор СПБ 1"},
            {"amo_user_id": 13984314, "user_name": "Администратор СПб 2"},
            {"amo_user_id": 14113038, "user_name": "Колл-центр СПБ2"},
        ]

        mapped = CallAnalytics._user_map(rows)

        self.assertEqual(set(mapped), set(USER_RULES_BY_ID))
        self.assertEqual(mapped[13984270]["label"], "Новое имя КЦ Москва")
        self.assertEqual(mapped[13984330]["team"], "cc")
        self.assertEqual(mapped[13984330]["city"], "СПБ")
        self.assertEqual(mapped[13984314]["team"], "admin")
        self.assertNotIn(14113038, mapped)


class YclientsMartTests(unittest.TestCase):
    def test_normalizes_branches_and_classifies_directions(self):
        self.assertEqual(normalize_branch("XS СПб Площадь Восстания"), "Невский")
        self.assertEqual(classify_direction(["Лазерная эпиляция 5 зон"]), "Лазер")
        self.assertEqual(classify_direction(["Микс массаж 55 минут"]), "Массаж")
        self.assertEqual(
            classify_direction(["Абонемент 10 по 60 мин УСИЛЕННЫЕ"]),
            "Массаж",
        )
        self.assertEqual(classify_direction(["Неизвестная услуга"]), "Не определено")

    def test_classifies_visit_types_by_record_color(self):
        self.assertEqual(classify_visit_type("#2196F3"), "first")
        self.assertEqual(classify_visit_type("00bcd4"), "first")
        self.assertEqual(classify_visit_type("4CAF50"), "one_off")
        self.assertEqual(classify_visit_type("ffeb3b"), "repeat")
        self.assertEqual(classify_visit_type(""), "repeat")
        self.assertEqual(classify_visit_type(None), "repeat")

    def test_matches_only_payment_administrator_and_exact_prepayment_services(self):
        self.assertTrue(is_administrator_staff(" Администратор "))
        self.assertFalse(is_administrator_staff("Администратор XS"))
        self.assertFalse(is_administrator_staff("Старший администратор"))
        self.assertEqual(
            administrator_prepayment_direction(["Предоплата (массаж)"]),
            "Массаж",
        )
        self.assertEqual(
            administrator_prepayment_direction(["предоплата (ЛАЗЕР)"]),
            "Лазер",
        )
        self.assertIsNone(
            administrator_prepayment_direction(["Предоплата на сеанс"])
        )

    def test_builds_color_reconciliation_and_filtered_registry(self):
        visits = [
            {
                "record_id": 1,
                "client_id": 101,
                "visit_at": "2026-08-01T10:00",
                "branch": "Свиблово",
                "direction": "Массаж",
                "record_color": "2196f3",
                "visit_type": "first",
                "visit_type_label": "Первичный",
                "uses_subscription": False,
                "service_text": "Массаж",
            },
            {
                "record_id": 2,
                "client_id": 102,
                "visit_at": "2026-08-02T10:00",
                "branch": "Свиблово",
                "direction": "Массаж",
                "record_color": "00bcd4",
                "visit_type": "first",
                "visit_type_label": "Первичный",
                "uses_subscription": False,
                "service_text": "Массаж",
            },
            {
                "record_id": 3,
                "client_id": 103,
                "visit_at": "2026-08-03T10:00",
                "branch": "Свиблово",
                "direction": "Массаж",
                "record_color": "4caf50",
                "visit_type": "one_off",
                "visit_type_label": "Разовый",
                "uses_subscription": False,
                "service_text": "Массаж",
            },
            {
                "record_id": 4,
                "client_id": 104,
                "visit_at": "2026-08-04T10:00",
                "branch": "Свиблово",
                "direction": "Массаж",
                "record_color": "",
                "visit_type": "repeat",
                "visit_type_label": "Повторный",
                "uses_subscription": True,
                "service_text": "Массаж",
            },
        ]
        result = build_color_reconciliation(
            visits,
            visit_types=["first"],
            colors=["2196f3"],
            per_page=20,
        )
        self.assertEqual(result["totals"]["visits"], 4)
        self.assertEqual(result["totals"]["classified_total"], 4)
        self.assertTrue(result["totals"]["is_balanced"])
        self.assertEqual(result["pagination"]["total"], 1)
        self.assertEqual(result["rows"][0]["record_id"], 1)
        self.assertEqual(len(result["colors"]), 4)
        payload = color_reconciliation_to_csv(result["rows"])
        self.assertIn("Рассчитанный тип".encode("utf-8"), payload)
        self.assertIn("#2196F3".encode("utf-8"), payload)

    def test_builds_operational_and_revenue_metrics_without_debt(self):
        visits = [
            {
                "record_id": 1,
                "visit_at": "2026-08-01T10:00",
                "branch": "Свиблово",
                "direction": "Массаж",
                "visit_type": "first",
                "uses_subscription": False,
            },
            {
                "record_id": 2,
                "visit_at": "2026-08-02T10:00",
                "branch": "Свиблово",
                "direction": "Массаж",
                "visit_type": "one_off",
                "uses_subscription": True,
            },
        ]
        transactions = [
            {
                "transaction_id": 11,
                "transaction_at": "2026-08-01T10:05",
                "branch": "Свиблово",
                "direction": "Массаж",
                "expense_title": "Оказание услуг",
                "amount": Decimal("2800"),
                "visit_type": "one_off",
            },
            {
                "transaction_id": 12,
                "transaction_at": "2026-08-02T10:05",
                "branch": "Свиблово",
                "direction": "Массаж",
                "expense_title": "Продажа абонементов",
                "amount": Decimal("10000"),
                "visit_type": "repeat",
            },
        ]
        sales = [
            {
                "document_id": 20,
                "branch": "Свиблово",
                "direction": "Массаж",
                "subscription_kind": "first",
            }
        ]
        result = build_yclients_report(visits, transactions, sales)
        self.assertEqual(result["kpi"]["visits"], 2)
        self.assertEqual(result["kpi"]["first_visits"], 1)
        self.assertEqual(result["kpi"]["repeat_visits"], 0)
        self.assertEqual(result["kpi"]["revenue"], 12800.0)
        self.assertEqual(result["kpi"]["subscription_sales"], 1)
        self.assertEqual(result["kpi"]["subscription_visits"], 1)
        self.assertEqual(result["kpi"]["one_off_visits"], 1)
        self.assertEqual(result["kpi"]["one_off_visits"], 1)
        self.assertEqual(
            result["kpi"]["first_visits"]
            + result["kpi"]["one_off_visits"]
            + result["kpi"]["repeat_visits"],
            result["kpi"]["visits"],
        )
        self.assertEqual(result["kpi"]["service_revenue"], 2800.0)
        self.assertEqual(
            result["kpi"]["service_revenue"]
            + result["kpi"]["subscription_revenue"],
            result["kpi"]["revenue"],
        )
        self.assertEqual(result["kpi"]["one_off_revenue"], 2800.0)

    def test_administrator_sales_remain_but_record_is_not_a_visit(self):
        visits = [
            {
                "record_id": 1,
                "visit_at": "2026-08-01T10:00",
                "branch": "Свиблово",
                "direction": "Массаж",
                "visit_type": "first",
                "uses_subscription": False,
                "is_administrator_record": True,
            },
            {
                "record_id": 2,
                "visit_at": "2026-08-02T10:00",
                "branch": "Свиблово",
                "direction": "Массаж",
                "visit_type": "repeat",
                "uses_subscription": False,
                "is_administrator_record": False,
            },
        ]
        transactions = [
            {
                "transaction_id": 11,
                "branch": "Свиблово",
                "direction": "Массаж",
                "expense_title": "Оказание услуг",
                "amount": Decimal("500"),
                "visit_type": "first",
                "is_administrator_record": True,
            },
            {
                "transaction_id": 12,
                "branch": "Свиблово",
                "direction": "Массаж",
                "expense_title": "Продажа абонементов",
                "amount": Decimal("10000"),
                "visit_type": "repeat",
                "is_administrator_record": True,
            },
        ]
        sales = [
            {
                "document_id": 20,
                "branch": "Свиблово",
                "direction": "Массаж",
                "subscription_kind": "first",
            }
        ]

        result = build_yclients_report(visits, transactions, sales)

        self.assertEqual(result["kpi"]["visits"], 1)
        self.assertEqual(result["kpi"]["first_visits"], 0)
        self.assertEqual(result["kpi"]["repeat_visits"], 1)
        self.assertEqual(result["kpi"]["revenue"], 10500.0)
        self.assertEqual(result["kpi"]["service_revenue"], 500.0)
        self.assertEqual(result["kpi"]["first_visit_revenue"], 500.0)
        self.assertEqual(result["kpi"]["subscription_revenue"], 10000.0)
        self.assertEqual(result["kpi"]["subscription_sales"], 1)
        self.assertEqual(result["quality"]["excluded_administrator_visits"], 1)

    def test_color_reconciliation_excludes_administrator_records(self):
        result = build_color_reconciliation(
            [
                {
                    "record_id": 1,
                    "branch": "Свиблово",
                    "direction": "Массаж",
                    "visit_type": "first",
                    "record_color": "2196f3",
                    "is_administrator_record": True,
                },
                {
                    "record_id": 2,
                    "branch": "Свиблово",
                    "direction": "Массаж",
                    "visit_type": "repeat",
                    "record_color": "",
                    "is_administrator_record": False,
                },
            ]
        )
        self.assertEqual(result["totals"]["visits"], 1)
        self.assertEqual(result["totals"]["repeat_visits"], 1)
        self.assertEqual(result["totals"]["excluded_administrator_records"], 1)

    def test_builds_installment_debt_reconciliation(self):
        rows = [
            {
                "record_id": 101,
                "visit_at": "2026-08-10T12:00",
                "branch": "Свиблово",
                "direction": "Массаж",
                "service_amount": Decimal("10000"),
                "paid_amount": Decimal("8000"),
                "payment_count": 1,
            },
            {
                "record_id": 102,
                "visit_at": "2026-08-11T12:00",
                "branch": "Академическая",
                "direction": "Лазер",
                "service_amount": Decimal("5000"),
                "paid_amount": Decimal("5000"),
                "payment_count": 2,
            },
        ]
        result = build_installment_debt_report(rows)
        self.assertEqual(result["totals"]["services"], 2)
        self.assertEqual(result["totals"]["service_amount"], 15000.0)
        self.assertEqual(result["totals"]["paid_amount"], 13000.0)
        self.assertEqual(result["totals"]["outstanding"], 2000.0)
        self.assertEqual(result["totals"]["payments"], 3)
        massage = next(row for row in result["rows"] if row["direction"] == "Массаж")
        self.assertEqual(massage["payment_status_key"], "partial")

        filtered = build_installment_debt_report(
            rows, filters={"branch": ["Свиблово"]}
        )
        self.assertEqual(filtered["totals"]["services"], 1)
        self.assertEqual(filtered["totals"]["service_amount"], 10000.0)


class MarketingExpenseLogicTests(unittest.TestCase):
    def test_rate_depends_on_source_and_direction(self):
        source, direction, cost, valid_from, comment = validate_rate_input(
            {
                "source": "Флоктори",
                "direction": "Лазер",
                "cost_per_lead": "550,50",
                "valid_from": "2026-08-01",
                "comment": "Новый договор",
            }
        )
        self.assertEqual((source, direction), ("Флоктори", "Лазер"))
        self.assertEqual(str(cost), "550.50")
        self.assertEqual(valid_from, date(2026, 8, 1))
        self.assertEqual(comment, "Новый договор")

    def test_new_fixed_price_sources_are_allowed(self):
        for source in ("РИС_xs", "Сайт Xsize", "SMM_xs"):
            validated = validate_rate_input(
                {
                    "source": source,
                    "direction": "Массаж",
                    "cost_per_lead": "650",
                    "valid_from": "2026-08-01",
                }
            )
            self.assertEqual(validated[0], source)

    def test_xsize_sources_do_not_accept_laser_rates(self):
        for source in ("РИС_xs", "Сайт Xsize", "SMM_xs"):
            with self.assertRaisesRegex(ValueError, "Массаж"):
                validate_rate_input(
                    {
                        "source": source,
                        "direction": "Лазер",
                        "cost_per_lead": "650",
                        "valid_from": "2026-08-01",
                    }
                )

    def test_speed_dial_is_not_available_for_expenses_or_budgets(self):
        with self.assertRaises(ValueError):
            validate_rate_input(
                {
                    "source": SPEED_DIAL_MASSAGE_SOURCE,
                    "direction": "Массаж",
                    "cost_per_lead": "500",
                    "valid_from": "2026-08-01",
                }
            )
        with self.assertRaises(ValueError):
            validate_period_input(
                {
                    "source": SPEED_DIAL_MASSAGE_SOURCE,
                    "branch": "Свиблово",
                    "direction": "Массаж",
                    "period_from": "2026-08-01",
                    "period_to": "2026-08-31",
                    "amount": "1000",
                },
                branches=["Свиблово"],
            )
        with self.assertRaises(ValueError):
            validate_budget_input(
                {
                    "source": SPEED_DIAL_MASSAGE_SOURCE,
                    "branch": "Свиблово",
                    "direction": "Массаж",
                    "budget_month": "2026-08",
                    "amount": "1000",
                },
                branches=["Свиблово"],
            )

    def test_vk_requires_branch_and_direction(self):
        with self.assertRaisesRegex(ValueError, "филиал"):
            validate_period_input(
                {
                    "source": "VK",
                    "period_from": "2026-08-01",
                    "period_to": "2026-08-31",
                    "amount": "100000",
                },
                branches=["Свиблово"],
            )

    def test_vk_expense_can_cover_a_single_day(self):
        expense = validate_period_input(
            {
                "source": "VK",
                "branch": "Свиблово",
                "direction": "Массаж",
                "period_from": "2026-08-11",
                "period_to": "2026-08-11",
                "amount": "2500",
            },
            branches=["Свиблово"],
        )
        self.assertEqual(expense.period_from, date(2026, 8, 11))
        self.assertEqual(expense.period_to, date(2026, 8, 11))

    def test_yandex_is_always_network_wide(self):
        expense = validate_period_input(
            {
                "source": "Яндекс.Карты",
                "branch": "Свиблово",
                "direction": "Массаж",
                "period_from": "2026-08-01",
                "period_to": "2026-08-31",
                "amount": "120000",
                "status": "confirmed",
            },
            branches=["Свиблово"],
        )
        self.assertIsNone(expense.branch)
        self.assertIsNone(expense.direction)

    def test_vk_preview_uses_branch_and_direction(self):
        records = [
            LeadRecord(1, date(2026, 8, 1), "Свиблово", "Массаж", "A", "VK", False),
            LeadRecord(2, date(2026, 8, 2), "Свиблово", "Лазер", "B", "VK", False),
            LeadRecord(3, date(2026, 8, 3), "Невский", "Массаж", "A", "VK", False),
        ]
        expense = validate_period_input(
            {
                "source": "VK",
                "branch": "Свиблово",
                "direction": "Массаж",
                "period_from": "2026-08-01",
                "period_to": "2026-08-31",
                "amount": "1000",
            },
            branches=["Свиблово", "Невский"],
        )
        preview = calculate_period_preview(records, expense)
        self.assertEqual(preview["matched_leads"], 1)
        self.assertEqual(preview["calculated_cpl"], 1000.0)

    def test_marketing_reconciliation_shows_raw_excluded_and_management(self):
        records = [
            MarketingTagRecord(
                1, date(2026, 8, 1), "Невский", "Массаж", "VK",
                "Свиблово", "Массаж", "VK",
            ),
            MarketingTagRecord(
                2, date(2026, 8, 2), "Невский", "Массаж", "VK",
                "Невский", "Массаж", "VK", "Дубль",
            ),
            MarketingTagRecord(
                3, date(2026, 8, 3), "Свиблово", "Массаж", "VK",
                "Свиблово", "Массаж", "VK",
            ),
        ]
        report = build_marketing_reconciliation(
            records,
            source="VK",
            branch="Невский",
            direction="Массаж",
            amo_subdomain="example",
        )
        row = report["rows"][0]
        self.assertEqual(row["tag_leads"], 2)
        self.assertEqual(row["excluded_leads"], 1)
        self.assertEqual(row["analytics_leads"], 1)
        self.assertEqual(row["management_leads"], 0)
        self.assertEqual(row["difference"], 1)
        self.assertEqual(
            row["deals"][0]["lead_url"],
            "https://example.amocrm.ru/leads/detail/1",
        )

    def test_performance_combines_fixed_rates_and_period_expenses(self):
        records = [
            LeadRecord(1, date(2026, 8, 1), "Свиблово", "Массаж", "A", "РИС", True),
            LeadRecord(2, date(2026, 8, 2), "Свиблово", "Массаж", "A", "VK", True),
            LeadRecord(3, date(2026, 8, 3), "Свиблово", "Массаж", "B", "VK", False),
        ]
        result = calculate_marketing_performance(
            records,
            records,
            rates=[
                {
                    "source": "РИС",
                    "direction": "Массаж",
                    "cost_per_lead": 500,
                    "valid_from": "2026-08-01",
                    "valid_to": None,
                }
            ],
            period_expenses=[
                {
                    "source": "VK",
                    "branch": "Свиблово",
                    "direction": "Массаж",
                    "period_from": "2026-08-01",
                    "period_to": "2026-08-31",
                    "amount": 1000,
                    "status": "confirmed",
                }
            ],
        )
        self.assertEqual(result["kpi"]["spend"], 1500.0)
        self.assertEqual(result["kpi"]["cpl"], 500.0)
        self.assertEqual(result["kpi"]["cost_per_booking"], 750.0)
        self.assertEqual(result["coverage"]["status"], "complete")
        self.assertEqual(len(result["groups"]["source_branch"]), 2)
        vk_branch = next(
            row
            for row in result["groups"]["source_branch"]
            if row["source"] == "VK"
        )
        self.assertEqual(vk_branch["branch"], "Свиблово")
        self.assertEqual(vk_branch["leads"], 2)
        self.assertEqual(vk_branch["spend"], 1000.0)

    def test_speed_dial_is_network_wide_in_marketing_report(self):
        records = [
            LeadRecord(
                10,
                date(2026, 8, 1),
                "Свиблово",
                "Массаж",
                "A",
                SPEED_DIAL_MASSAGE_SOURCE,
                True,
            )
        ]
        result = calculate_marketing_performance(
            records,
            records,
            rates=[],
            period_expenses=[],
        )
        self.assertEqual(result["groups"]["source_branch"], [])
        self.assertEqual(
            result["groups"]["branch"][0]["label"],
            "Без привязки к филиалу",
        )
        self.assertEqual(result["coverage"]["unconfigured_sources"], [])

    def test_period_expense_is_allocated_before_report_filter(self):
        all_records = [
            LeadRecord(1, date(2026, 8, 1), "Свиблово", "Массаж", "A", "VK", True),
            LeadRecord(2, date(2026, 8, 2), "Свиблово", "Массаж", "A", "VK", False),
        ]
        result = calculate_marketing_performance(
            all_records[:1],
            all_records,
            rates=[],
            period_expenses=[
                {
                    "source": "VK",
                    "branch": "Свиблово",
                    "direction": "Массаж",
                    "period_from": "2026-08-01",
                    "period_to": "2026-08-31",
                    "amount": 1000,
                    "status": "confirmed",
                }
            ],
        )
        self.assertEqual(result["kpi"]["spend"], 500.0)

    def test_missing_expense_is_visible_as_partial_coverage(self):
        records = [
            LeadRecord(1, date(2026, 8, 1), "Свиблово", "Лазер", "A", "VK", False),
        ]
        result = calculate_marketing_performance(
            records, records, rates=[], period_expenses=[]
        )
        self.assertEqual(result["coverage"]["status"], "partial")
        self.assertIsNone(result["groups"]["source"][0]["spend"])


class BudgetPlanningLogicTests(unittest.TestCase):
    def test_budget_scope_requires_source_branch_direction_and_month(self):
        budget = validate_budget_input(
            {
                "source": "РИС",
                "branch": "Невский",
                "direction": "Массаж",
                "budget_month": "2026-08",
                "amount": "100000",
            },
            branches=["Невский"],
        )
        self.assertEqual(budget.budget_month, date(2026, 8, 1))
        self.assertEqual(str(budget.amount), "100000.00")

    def test_excluded_sources_cannot_have_budgets(self):
        for source in ("Яндекс.Карты", "Сайт Xsize", "SMM_xs"):
            with self.assertRaisesRegex(ValueError, "VK, РИС, Флоктори или РИС_xs"):
                validate_budget_input(
                    {
                        "source": source,
                        "branch": "Невский",
                        "direction": "Массаж",
                        "budget_month": "2026-08",
                        "amount": "1000",
                    },
                    branches=["Невский"],
                )

    def test_ris_xs_budget_is_limited_to_three_branches_and_massage(self):
        budget = validate_budget_input(
            {
                "source": "РИС_xs",
                "branch": "Академическая",
                "direction": "Массаж",
                "budget_month": "2026-08",
                "amount": "25000",
            },
            branches=["Академическая", "Невский", "Комендантский", "Свиблово"],
        )
        self.assertEqual(budget.source, "РИС_xs")

        with self.assertRaisesRegex(ValueError, "только филиалы"):
            validate_budget_input(
                {
                    "source": "РИС_xs",
                    "branch": "Свиблово",
                    "direction": "Массаж",
                    "budget_month": "2026-08",
                    "amount": "25000",
                },
                branches=["Академическая", "Невский", "Комендантский", "Свиблово"],
            )

        with self.assertRaisesRegex(ValueError, "только направление"):
            validate_budget_input(
                {
                    "source": "РИС_xs",
                    "branch": "Невский",
                    "direction": "Лазер",
                    "budget_month": "2026-08",
                    "amount": "25000",
                },
                branches=["Академическая", "Невский", "Комендантский"],
            )

    def test_ris_xs_actual_spend_uses_fixed_lead_rate(self):
        result = calculate_budget_rows(
            [LeadRecord(1, date(2026, 8, 2), "Невский", "Массаж", "A", "РИС_xs", False)],
            [{
                "source": "РИС_xs",
                "branch": "Невский",
                "direction": "Массаж",
                "amount": 5000,
            }],
            rates=[{
                "source": "РИС_xs",
                "direction": "Массаж",
                "cost_per_lead": 525,
                "valid_from": "2026-08-01",
                "valid_to": None,
            }],
            period_expenses=[],
            month_start=date(2026, 8, 1),
            month_end=date(2026, 8, 31),
        )
        self.assertEqual(result["rows"][0]["spent"], 525.0)
        self.assertEqual(result["rows"][0]["remaining"], 4475.0)

    def test_budget_rows_compare_plan_with_fixed_and_period_spend(self):
        records = [
            LeadRecord(1, date(2026, 8, 2), "Невский", "Массаж", "A", "РИС", False),
            LeadRecord(2, date(2026, 8, 3), "Невский", "Массаж", "B", "РИС", True),
        ]
        result = calculate_budget_rows(
            records,
            [
                {
                    "source": "РИС",
                    "branch": "Невский",
                    "direction": "Массаж",
                    "amount": 5000,
                },
                {
                    "source": "VK",
                    "branch": "Невский",
                    "direction": "Лазер",
                    "amount": 10000,
                },
            ],
            rates=[
                {
                    "source": "РИС",
                    "direction": "Массаж",
                    "cost_per_lead": 500,
                    "valid_from": "2026-08-01",
                    "valid_to": None,
                }
            ],
            period_expenses=[
                {
                    "source": "VK",
                    "branch": "Невский",
                    "direction": "Лазер",
                    "period_from": "2026-08-01",
                    "period_to": "2026-08-31",
                    "amount": 3100,
                    "status": "confirmed",
                }
            ],
            month_start=date(2026, 8, 1),
            month_end=date(2026, 8, 31),
        )
        ris = next(row for row in result["rows"] if row["source"] == "РИС")
        vk = next(row for row in result["rows"] if row["source"] == "VK")
        self.assertEqual((ris["spent"], ris["remaining"]), (1000.0, 4000.0))
        self.assertEqual((vk["spent"], vk["remaining"]), (3100.0, 6900.0))
        self.assertEqual(result["totals"]["budget"], 15000.0)
        self.assertEqual(result["totals"]["spent"], 4100.0)

    def test_vk_cross_month_expense_is_prorated_by_days(self):
        result = calculate_budget_rows(
            [],
            [],
            rates=[],
            period_expenses=[
                {
                    "source": "VK",
                    "branch": "Свиблово",
                    "direction": "Массаж",
                    "period_from": "2026-07-15",
                    "period_to": "2026-08-14",
                    "amount": 3100,
                    "status": "confirmed",
                }
            ],
            month_start=date(2026, 8, 1),
            month_end=date(2026, 8, 31),
        )
        self.assertEqual(result["rows"][0]["spent"], 1400.0)


class TrendAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            LeadRecord(1, date(2026, 8, 1), "Невский", "Массаж", "A", "РИС", True),
            LeadRecord(2, date(2026, 8, 2), "Невский", "Массаж", "A", "РИС", False),
            LeadRecord(3, date(2026, 8, 10), "Свиблово", "Лазер", "B", "VK", True),
        ]
        self.rates = [
            {
                "source": "РИС",
                "direction": "Массаж",
                "cost_per_lead": 500,
                "valid_from": "2026-08-01",
                "valid_to": None,
            }
        ]

    def test_daily_trend_contains_result_and_cost_metrics(self):
        result = calculate_trend_data(
            self.records,
            self.records,
            rates=self.rates,
            period_expenses=[],
            start=date(2026, 8, 1),
            end=date(2026, 8, 10),
            granularity="day",
            breakdown="none",
        )
        self.assertEqual(len(result["buckets"]), 10)
        first = result["series"][0]["points"][0]
        self.assertEqual((first["leads"], first["bookings"]), (1, 1))
        self.assertEqual((first["spend"], first["cpl"]), (500.0, 500.0))
        self.assertEqual(result["summary"]["conversion"], 66.67)

    def test_weekly_and_monthly_grouping(self):
        weekly = calculate_trend_data(
            self.records,
            self.records,
            rates=self.rates,
            period_expenses=[],
            start=date(2026, 8, 1),
            end=date(2026, 8, 31),
            granularity="week",
            breakdown="source",
        )
        monthly = calculate_trend_data(
            self.records,
            self.records,
            rates=self.rates,
            period_expenses=[],
            start=date(2026, 8, 1),
            end=date(2026, 8, 31),
            granularity="month",
            breakdown="direction",
        )
        self.assertEqual(len(weekly["buckets"]), 6)
        self.assertEqual({row["label"] for row in weekly["series"]}, {"VK", "РИС"})
        self.assertEqual(len(monthly["buckets"]), 1)
        self.assertEqual({row["label"] for row in monthly["series"]}, {"Лазер", "Массаж"})


class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.analytics = object.__new__(DashboardAnalytics)
        self.records = [
            LeadRecord(1, date(2026, 7, 1), "Академическая", "Массаж", "A", "VK", True),
            LeadRecord(2, date(2026, 7, 2), "Академическая", "Массаж", "A", "VK", False),
            LeadRecord(3, date(2026, 7, 3), "Свиблово", "Лазер", "B", "РИС", True),
        ]

    def test_weighted_conversion_and_groups(self):
        result = self.analytics.aggregate(self.records)
        self.assertEqual(result["kpi"]["leads"], 3)
        self.assertEqual(result["kpi"]["bookings"], 2)

    def test_excluded_loss_reasons_are_exact(self):
        self.assertEqual(
            set(EXCLUDED_LOSS_REASONS.values()),
            {"Другой город", "Дубль", "Спам / не лид", "Тест"},
        )

    def test_filter(self):
        result = self.analytics.aggregate(
            self.records, {"branch": "Академическая"}
        )
        self.assertEqual(result["kpi"]["leads"], 2)
        self.assertEqual(result["kpi"]["bookings"], 1)

    def test_multiple_values_in_one_filter(self):
        result = self.analytics.aggregate(
            self.records,
            {"branch": ["Академическая", "Свиблово"]},
        )
        self.assertEqual(result["kpi"]["leads"], 3)
        self.assertEqual(result["kpi"]["bookings"], 2)

    def test_speed_dial_is_network_wide_and_not_attributed_to_branch(self):
        records = [
            LeadRecord(
                10,
                date(2026, 8, 1),
                "Невский",
                "Массаж",
                "A",
                SPEED_DIAL_MASSAGE_SOURCE,
                True,
            )
        ]
        result = self.analytics.aggregate(records)
        self.assertEqual(result["branches"][0]["branch"], "Без привязки к филиалу")
        self.assertEqual(
            self.analytics.aggregate(records, {"branch": "Невский"})["kpi"]["leads"],
            0,
        )


class FunnelAnalyticsTests(unittest.TestCase):
    def test_administrator_record_keeps_sales_but_does_not_count_as_visit(self):
        now = datetime(2026, 8, 10, 12, 0)
        base = {
            "attendance": 1,
            "is_deleted": 0,
            "datetime": datetime(2026, 8, 1, 10, 0),
        }
        self.assertFalse(
            record_counts_as_visit(
                {**base, "staff_name": "Администратор"},
                now,
            )
        )
        self.assertTrue(
            record_counts_as_visit(
                {**base, "staff_name": "Администратор XS"},
                now,
            )
        )

    def test_roi_and_revenue_are_grouped_by_marketing_dimensions(self):
        records = [
            LeadRecord(1, date(2026, 8, 1), "Невский", "Массаж", "A", "VK", True),
            LeadRecord(2, date(2026, 8, 2), "Невский", "Массаж", "A", "VK", False),
        ]
        outcomes = {
            1: LeadOutcome(
                linked=True,
                visited=True,
                sales=1,
                service_sales=1,
                revenue=Decimal("5000"),
                service_revenue=Decimal("5000"),
            )
        }
        result = calculate_funnel_report(
            records,
            outcomes,
            {1: Decimal("500"), 2: Decimal("500")},
            {1, 2},
        )
        self.assertEqual(result["kpi"]["revenue"], 5000.0)
        self.assertEqual(result["kpi"]["spend"], 1000.0)
        self.assertEqual(result["kpi"]["roi"], 400.0)
        self.assertEqual(result["kpi"]["roi_revenue"], 5000.0)
        self.assertEqual(result["kpi"]["marketing_profit"], 4000.0)
        self.assertEqual(result["kpi"]["cost_per_lead"], 500.0)
        self.assertEqual(result["kpi"]["visits"], 1)
        self.assertEqual(result["groups"]["source"][0]["label"], "VK")
        self.assertEqual(result["revenue_mix"][0]["revenue"], 5000.0)

    def test_roi_uses_only_revenue_covered_by_marketing_expenses(self):
        records = [
            LeadRecord(1, date(2026, 8, 1), "Невский", "Массаж", "A", "VK", True),
            LeadRecord(2, date(2026, 8, 2), "Невский", "Массаж", "A", "VK", True),
        ]
        outcomes = {
            1: LeadOutcome(linked=True, visited=True, sales=1, revenue=Decimal("5000")),
            2: LeadOutcome(linked=True, visited=True, sales=1, revenue=Decimal("9000")),
        }
        result = calculate_funnel_report(
            records,
            outcomes,
            {1: Decimal("500")},
            {1},
        )
        self.assertEqual(result["kpi"]["revenue"], 14000.0)
        self.assertEqual(result["kpi"]["roi_revenue"], 5000.0)
        self.assertEqual(result["kpi"]["roi"], 900.0)
        self.assertEqual(result["kpi"]["marketing_profit"], 4500.0)
        self.assertEqual(result["kpi"]["cost_per_lead"], 500.0)
        self.assertEqual(result["kpi"]["cost_per_booking"], 500.0)
        self.assertEqual(result["kpi"]["cost_per_visit"], 500.0)
        self.assertEqual(result["kpi"]["cost_per_sale"], 500.0)
        self.assertEqual(result["kpi"]["expense_coverage"], 50.0)

    def test_speed_dial_has_no_branch_children_or_expense_metrics(self):
        records = [
            LeadRecord(
                10,
                date(2026, 8, 1),
                "Свиблово",
                "Массаж",
                "A",
                SPEED_DIAL_MASSAGE_SOURCE,
                True,
            )
        ]
        result = calculate_funnel_report(
            records,
            {10: LeadOutcome(linked=True, visited=True, revenue=Decimal("5000"))},
            {},
            set(),
        )
        source = result["groups"]["source"][0]
        self.assertEqual(source["label"], SPEED_DIAL_MASSAGE_SOURCE)
        self.assertIsNone(source["spend"])
        self.assertIsNone(source["roi"])
        self.assertIsNone(source["expense_coverage"])
        self.assertEqual(result["groups"]["source_branch"], [])
        self.assertEqual(
            result["groups"]["branch"][0]["label"],
            "Без привязки к филиалу",
        )

    def test_branch_filter_excludes_network_wide_speed_dial(self):
        records = [
            LeadRecord(
                10,
                date(2026, 8, 1),
                "Свиблово",
                "Массаж",
                "A",
                SPEED_DIAL_MASSAGE_SOURCE,
                True,
            )
        ]
        result = calculate_funnel_report(
            records,
            {},
            {},
            set(),
            filters={"branch": ["Свиблово"]},
        )
        self.assertEqual(result["kpi"]["leads"], 0)


class VerificationTests(unittest.TestCase):
    def test_last_sync_uses_successful_run_utc_time(self):
        class FakeCursor:
            def __init__(self):
                self.query = ""

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, query):
                self.query = query

            def fetchone(self):
                return {"synced_at": datetime(2026, 8, 13, 21, 0, 15)}

        class FakeConnection:
            def __init__(self):
                self.cursor_instance = FakeCursor()

            def cursor(self):
                return self.cursor_instance

            def close(self):
                return None

        connection = FakeConnection()
        with patch("dashboard.analytics.load_env", return_value={}):
            analytics = DashboardAnalytics()
        analytics._database = lambda: connection

        self.assertEqual(analytics.last_sync(), "2026-08-14 00:00")
        self.assertIn("status = 'success'", connection.cursor_instance.query)

    def setUp(self):
        self.analytics = object.__new__(DashboardAnalytics)
        self.analytics.amo_subdomain = "example"
        self.records = [
            VerificationRecord(
                1,
                "Лид 1",
                datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
                date(2026, 8, 2),
                "Академическая",
                "VK",
                "Москва",
                "Клиент записан",
                ("ВК", "массаж"),
                True,
            ),
            VerificationRecord(
                2,
                "Лид 2",
                datetime(2026, 8, 2, 10, 0, tzinfo=timezone.utc),
                None,
                "Академическая",
                "VK",
                "Москва",
                "Новая заявка",
                ("ВК",),
                False,
            ),
            VerificationRecord(
                3,
                "Лид 3",
                datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc),
                None,
                "Свиблово",
                "РИС",
                "Москва",
                "Клиент пришел",
                ("РИС",),
                True,
            ),
        ]
        self.analytics.verification_records = lambda start, end: self.records

    def test_utc_timestamp_is_presented_in_moscow_timezone(self):
        value = DashboardAnalytics._moscow_datetime(
            datetime(2026, 8, 1, 19, 20)
        )
        self.assertEqual(value.isoformat(timespec="minutes"), "2026-08-01T22:20+03:00")

    def test_booking_stage_event_has_priority_over_custom_field_date(self):
        result = DashboardAnalytics._booking_date(
            datetime(2026, 8, 2, 8, 32, 24),
            date(2026, 8, 1),
        )
        self.assertEqual(result, date(2026, 8, 2))

    def test_custom_field_date_is_fallback_when_history_is_missing(self):
        result = DashboardAnalytics._booking_date(None, date(2026, 8, 1))
        self.assertEqual(result, date(2026, 8, 1))

    def test_merged_lead_inherits_earliest_booking_date(self):
        result = DashboardAnalytics._booking_date(
            None,
            date(2026, 8, 3),
            datetime(2026, 7, 29, 9, 25, 13),
            datetime(2026, 8, 1, 14, 2, 4),
        )
        self.assertEqual(result, date(2026, 8, 1))

    def test_booking_date_cannot_precede_lead_creation(self):
        result = DashboardAnalytics._booking_date(
            None,
            date(2026, 8, 2),
            datetime(2026, 8, 3, 16, 8, 34),
        )
        self.assertEqual(result, date(2026, 8, 3))

    def test_leads_are_all_created_deals(self):
        result = self.analytics.verification(
            date(2026, 8, 1), date(2026, 8, 31), view="leads"
        )
        self.assertEqual(result["counts"]["leads"], 3)
        self.assertEqual(result["counts"]["bookings"], 2)
        self.assertEqual(result["pagination"]["total"], 3)

    def test_booking_date_filter_uses_stage_date(self):
        result = self.analytics.verification(
            date(2026, 8, 1),
            date(2026, 8, 31),
            view="bookings",
            filters={"branch": ["Академическая"]},
            booking_start=date(2026, 8, 2),
            booking_end=date(2026, 8, 2),
        )
        self.assertEqual(result["counts"], {"leads": 2, "bookings": 1})
        self.assertEqual(result["rows"][0]["lead_id"], 1)
        self.assertEqual(result["rows"][0]["booking_date"], "2026-08-02")
        self.assertIn("/leads/detail/1", result["rows"][0]["lead_url"])

    def test_verification_allows_speed_dial_branch_filter(self):
        self.analytics.verification_records = lambda start, end: [
            VerificationRecord(
                37002495,
                "Сделка из YCLIENTS",
                datetime(2026, 7, 28, 10, 2, tzinfo=timezone.utc),
                date(2026, 8, 3),
                "Невский",
                SPEED_DIAL_MASSAGE_SOURCE,
                "СПБ",
                "перезапись",
                ("Скорозвон", "массаж", "СПБ Невский"),
                True,
            )
        ]
        result = self.analytics.verification(
            date(2026, 7, 28),
            date(2026, 8, 31),
            view="bookings",
            filters={"branch": ["Невский"]},
            booking_start=date(2026, 8, 3),
            booking_end=date(2026, 8, 3),
        )
        self.assertEqual(result["pagination"]["total"], 1)
        self.assertEqual(result["rows"][0]["lead_id"], 37002495)


class PlanFactLogicTests(unittest.TestCase):
    def test_receivables_match_operational_installment_cost_for_each_scope(self):
        from dashboard.yclients_analytics import build_installment_debt_report

        # Fully paid, partially paid and unpaid contributions all count at cost.
        debt_rows = [
            {"branch": "Академическая", "direction": "Массаж", "service_amount": "124600", "paid_amount": "124600"},
            {"branch": "Свиблово", "direction": "Массаж", "service_amount": "40000", "paid_amount": "10000"},
            {"branch": "Свиблово", "direction": "Лазер", "service_amount": "10400", "paid_amount": "0"},
        ]
        repository = Mock()
        repository.booking_events.return_value = []
        repository.sync_issues.return_value = []
        with TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.touch()
            analytics = PlanFactAnalytics(str(env_path), repository=repository)
            analytics._booking_candidates = Mock(return_value=[])
            analytics._eligible_record_events = Mock(return_value=({}, []))
            analytics._lead_primitives = Mock(return_value={})
            analytics._primary_rows = Mock(return_value=([], {}, set(), []))
            analytics._subscription_primitives = Mock(return_value=({}, []))
            analytics.yclients = Mock()
            analytics.yclients.report.return_value = {"groups": []}
            analytics.yclients._installment_services.return_value = debt_rows
            for branches, directions, expected in [
                ((), (), 175000),
                (("Академическая",), (), 124600),
                (("Свиблово",), ("Лазер",), 10400),
                (("Академическая",), ("Лазер",), 0),
            ]:
                with self.subTest(branches=branches, directions=directions):
                    report = analytics.report(date(2026, 9, 1), [], branches=branches, directions=directions)
                    actual = next(row["actual"] for row in report["metrics"] if row["code"] == "receivables")
                    selected = [row for row in debt_rows if (not branches or row["branch"] in branches) and (not directions or row["direction"] in directions)]
                    operational = build_installment_debt_report(selected)
                    self.assertEqual(actual, expected)
                    self.assertEqual(actual, operational["totals"]["service_amount"])
            analytics.yclients._installment_services.assert_called_with(date(2026, 9, 1), date(2026, 9, 30))

    def manual_values(self):
        return {
            "revenue": 500000,
            "receivables": 25000,
            "leads": 137,
            "lead_to_booking_cr": 35,
            "booking_to_visit_cr": 50,
            "primary_avg_check": 1800,
            "primary_subscription_cr": 27,
            "primary_subscription_initial_avg": 10000,
            "primary_subscription_full_avg": 20000,
            "repeat_visits": 17,
            "repeat_subscription_cr": 50,
            "repeat_subscription_initial_avg": 6000,
            "repeat_subscription_full_avg": 15000,
            "one_off_count": 9,
            "one_off_avg_check": 2100,
        }

    def test_plan_uses_half_up_rounding_and_rounded_dependencies(self):
        result = calculate_plan_values(self.manual_values())
        self.assertEqual(result["bookings"], Decimal("48.0000"))
        self.assertEqual(result["primary_visits"], Decimal("24.0000"))
        self.assertEqual(result["primary_subscription_count"], Decimal("6.0000"))
        self.assertEqual(result["repeat_subscription_count"], Decimal("9.0000"))
        self.assertEqual(result["primary_subscription_sum"], Decimal("60000.0000"))
        self.assertEqual(result["one_off_sum"], Decimal("18900.0000"))

    def test_actual_conversions_allow_different_populations(self):
        result = derive_actual_metrics({
            "branch": "Свиблово",
            "direction": "Массаж",
            "leads": 2,
            "bookings": 3,
            "primary_visits": 2,
        })
        self.assertEqual(result["lead_to_booking_cr"], Decimal("150.0000"))
        self.assertEqual(result["lead_to_visit_cr"], Decimal("100.0000"))

    def test_aggregate_recalculates_ratios_instead_of_averaging_them(self):
        result = aggregate_metric_values(
            [
                {"leads": 10, "bookings": 5, "primary_visits": 4},
                {"leads": 1, "bookings": 1, "primary_visits": 1},
            ],
            actual=True,
        )
        self.assertEqual(result["lead_to_booking_cr"], Decimal("54.5455"))
        self.assertEqual(result["booking_to_visit_cr"], Decimal("83.3333"))

    def test_plan_fact_source_overrides_are_isolated(self):
        regular = TagClassifier().classify(["Звонок", "массаж", "Свиблово"])
        regular_other = TagClassifier().classify(["прочее", "массаж", "Свиблово"])
        analytics = PlanFactAnalytics.__new__(PlanFactAnalytics)
        analytics.classifier = TagClassifier()
        plan_fact = analytics._classify(["Звонок", "массаж", "Свиблово"])
        plan_fact_other = analytics._classify(["прочее", "массаж", "Свиблово"])
        self.assertEqual(regular["source"], "Не определено")
        self.assertEqual(regular_other["source"], "Не определено")
        self.assertEqual(plan_fact["source"], "Звонок")
        self.assertEqual(plan_fact_other["source"], "Прочее")

    def test_plan_fact_starts_in_august_2026(self):
        self.assertEqual(parse_plan_month("2026-08"), date(2026, 8, 1))
        with self.assertRaises(ValueError):
            parse_plan_month("2026-07")


class ApplicationSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        env_path = Path(self.temp_dir.name) / ".env"
        env_path.write_text(
            "\n".join(
                [
                    "AMO_DB_HOST=localhost",
                    "AMO_DB_PORT=3306",
                    "AMO_DB_NAME=test",
                    "AMO_DB_USER=test",
                    "AMO_DB_PASSWORD=test",
                    "DASHBOARD_AUTH_REQUIRED=true",
                    "DASHBOARD_USERNAME=test-user",
                    "DASHBOARD_PASSWORD=test-password-123",
                    "DASHBOARD_SECRET_KEY=test-session-secret",
                    "DASHBOARD_COOKIE_SECURE=false",
                ]
            ),
            encoding="utf-8",
        )
        app = create_app(str(env_path))
        app.testing = True
        self.app = app
        self.client = app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def login(self, password="test-password-123"):
        self.client.get("/login")
        with self.client.session_transaction() as current_session:
            csrf_token = current_session["login_csrf"]
        return self.client.post(
            "/login",
            data={
                "username": "test-user",
                "password": password,
                "csrf_token": csrf_token,
                "next": "/",
            },
        )

    def test_health_and_page_render(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        protected = self.client.get("/")
        self.assertEqual(protected.status_code, 302)
        self.assertIn("/login", protected.headers["Location"])
        self.assertEqual(self.client.get("/api/dashboard").status_code, 401)
        self.assertEqual(self.client.get("/verification").status_code, 302)
        self.assertEqual(self.client.get("/expenses").status_code, 302)
        self.assertEqual(self.client.get("/budgets").status_code, 302)
        self.assertEqual(self.client.get("/plans").status_code, 302)
        self.assertEqual(self.client.get("/calls").status_code, 302)
        self.assertEqual(self.client.get("/call-recordings-demo").status_code, 302)
        self.assertEqual(self.client.get("/funnel").status_code, 302)
        self.assertEqual(self.client.get("/yclients").status_code, 302)
        self.assertEqual(self.client.get("/yclients/plan-fact").status_code, 302)
        self.assertEqual(self.client.get("/api/verification").status_code, 401)
        self.assertEqual(self.client.get("/api/expenses").status_code, 401)
        self.assertEqual(self.client.get("/api/budgets").status_code, 401)
        self.assertEqual(self.client.get("/api/plans").status_code, 401)
        self.assertEqual(self.client.get("/api/trends").status_code, 401)
        self.assertEqual(self.client.get("/api/calls").status_code, 401)
        self.assertEqual(self.client.get("/api/call-recordings-demo").status_code, 401)
        self.assertEqual(self.client.get("/api/funnel").status_code, 401)
        self.assertEqual(self.client.get("/api/yclients").status_code, 401)
        self.assertEqual(self.client.get("/api/yclients/plan-fact").status_code, 401)
        self.assertEqual(self.client.get("/api/yclients/colors").status_code, 401)
        self.assertEqual(self.client.get("/api/yclients/colors/export.csv").status_code, 401)
        failed_login = self.login(password="wrong-password")
        self.assertEqual(failed_login.status_code, 401)
        self.assertIn("Неверный логин или пароль".encode("utf-8"), failed_login.data)
        self.assertEqual(self.login().status_code, 302)
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Управленческая аналитика".encode("utf-8"), page.data)
        self.assertNotIn(b"brand-mark", page.data)
        self.assertNotIn(b"periodSummary", page.data)
        self.assertIn("Какие источники приводят лидов".encode("utf-8"), page.data)
        self.assertIn("чтобы увидеть его филиалы".encode("utf-8"), page.data)
        self.assertIn(b"data-expand-source", page.data)
        self.assertIn(b'sourceMetric: "conversion"', page.data)
        self.assertIn(b'data-source-metric="conversion" class="active"', page.data)
        self.assertIn(b'id="trendTitle"', page.data)
        self.assertIn(b'id="trendChart"', page.data)
        self.assertIn(b'data-trend-granularity="day"', page.data)
        self.assertNotIn("Время до записи".encode("utf-8"), page.data)
        self.assertGreater(page.data.find(b'id="trendTitle"'), page.data.find(b'id="branchesBody"'))
        self.assertLess(page.data.find(b'id="sourceVolumeChart"'), page.data.find(b'id="offersChart"'))
        self.assertEqual(page.data.count(b"data-branch-sort="), 4)
        self.assertNotIn(b"progress-column", page.data)
        self.assertNotIn(b"mini-track", page.data)
        self.assertNotIn("Таблица для проверки и выгрузки".encode("utf-8"), page.data)
        self.assertNotIn(b"detailsBody", page.data)

        self.assertNotIn(b"csvExport", page.data)
        self.assertIn(b"<style>", page.data)
        self.assertIn(b'<script nonce="', page.data)
        self.assertNotIn(b'href="/static/styles.css"', page.data)
        self.assertNotIn(b'src="/static/app.js"', page.data)
        self.assertEqual(page.headers["X-Frame-Options"], "DENY")
        self.assertIn("no-store", page.headers["Cache-Control"])
        self.assertIn("Какие сделки исключены".encode("utf-8"), page.data)
        self.assertIn("Спам / не лид".encode("utf-8"), page.data)
        verification_page = self.client.get("/verification")
        self.assertEqual(verification_page.status_code, 200)
        self.assertIn(
            "Сверка заявок и записей".encode("utf-8"),
            verification_page.data,
        )
        self.assertIn(b'data-view="leads"', verification_page.data)
        self.assertIn(b'data-view="bookings"', verification_page.data)
        self.assertIn("Дата записи".encode("utf-8"), verification_page.data)
        self.assertIn("Какие сделки исключены".encode("utf-8"), verification_page.data)
        self.assertIn(b'timeZone: "Europe/Moscow"', verification_page.data)
        self.assertIn(b'data-today=', verification_page.data)
        expenses_page = self.client.get("/expenses")
        self.assertEqual(expenses_page.status_code, 200)
        self.assertIn("Расходы на маркетинг".encode("utf-8"), expenses_page.data)
        self.assertIn(
            "Тарифы источников с фиксированной ценой лида".encode("utf-8"),
            expenses_page.data,
        )
        self.assertIn('value="РИС_xs"'.encode("utf-8"), expenses_page.data)
        self.assertIn('value="Сайт Xsize"'.encode("utf-8"), expenses_page.data)
        self.assertIn('value="SMM_xs"'.encode("utf-8"), expenses_page.data)
        self.assertIn(b'name="period_from"', expenses_page.data)
        self.assertIn(b'name="period_to"', expenses_page.data)
        self.assertIn(b'id="periodFrom"', expenses_page.data)
        self.assertIn(b'id="periodTo"', expenses_page.data)
        self.assertIn(b'id="expenseListBranch"', expenses_page.data)
        self.assertIn(b'id="expenseListDirection"', expenses_page.data)
        self.assertIn(b'id="expenseListDateFrom"', expenses_page.data)
        self.assertIn(b'id="expenseListDateTo"', expenses_page.data)
        self.assertIn(b'id="expenseListReset"', expenses_page.data)
        self.assertIn(b"filteredPeriodExpenses", expenses_page.data)
        period_form = expenses_page.data.split(b'id="periodExpenseForm"', 1)[1].split(b'</form>', 1)[0]
        self.assertNotIn(b'name="comment"', period_form)
        self.assertLess(period_form.find(b'id="periodFrom"'), period_form.find(b'id="periodSource"'))
        self.assertLess(period_form.find(b'id="periodTo"'), period_form.find(b'id="periodSource"'))
        self.assertIn(b'data-csrf-token=', expenses_page.data)
        self.assertIn(b'data-today=', expenses_page.data)
        self.assertIn("Свиблово".encode("utf-8"), expenses_page.data)
        budgets_page = self.client.get("/budgets")
        self.assertEqual(budgets_page.status_code, 200)
        self.assertIn("Планирование бюджета".encode("utf-8"), budgets_page.data)
        self.assertIn("Бюджет, расход и остаток".encode("utf-8"), budgets_page.data)
        self.assertIn(b'id="budgetViewMonth"', budgets_page.data)
        self.assertIn(b'id="budgetRows"', budgets_page.data)
        self.assertIn(b'id="budgetHistoryRows"', budgets_page.data)
        self.assertIn(b'id="budgetHistoryTray"', budgets_page.data)
        self.assertNotIn(b'<section class="panel budget-history-panel"', budgets_page.data)
        self.assertIn(b'id="budgetCancelEdit"', budgets_page.data)
        self.assertIn("РИС_xs".encode("utf-8"), budgets_page.data)
        self.assertIn(b"budgetEntryBranchesBySource", budgets_page.data)
        self.assertIn(b"updateBudgetEntryOptions", budgets_page.data)
        self.assertIn(b'data-budget-edit=', budgets_page.data)
        self.assertIn(b'data-budget-delete=', budgets_page.data)
        self.assertIn(b'data-csrf-token=', budgets_page.data)
        self.assertIn(b"const form = event.currentTarget;", budgets_page.data)

        plans_page = self.client.get("/plans")
        self.assertEqual(plans_page.status_code, 200)
        self.assertIn("Ввод плана".encode("utf-8"), plans_page.data)
        self.assertIn(b'id="planMetricSections"', plans_page.data)
        self.assertIn(b"calculatePlanPreview", plans_page.data)

        plan_fact_page = self.client.get("/yclients/plan-fact")
        self.assertEqual(plan_fact_page.status_code, 200)
        self.assertIn("План–факт".encode("utf-8"), plan_fact_page.data)
        self.assertIn(b'id="planFactMetricRows"', plan_fact_page.data)
        self.assertIn(b'id="planFactIssueRows"', plan_fact_page.data)

        expenses_page = self.client.get("/expenses")
        self.assertIn(b"const formElement = event.currentTarget;", expenses_page.data)
        self.assertIn(b"setExpenseFormSubmitting(formElement, true);", expenses_page.data)
        self.assertIn(b"updateRateDirections();", expenses_page.data)
        self.assertIn(b'id="marketingReconciliationTray"', expenses_page.data)
        self.assertNotIn(b'id="marketingReconciliationTray" open', expenses_page.data)
        self.assertIn(b"/api/expenses/reconciliation", expenses_page.data)
        self.assertIn(b"setBudgetFormSubmitting(form, true);", budgets_page.data)
        calls_page = self.client.get("/calls")
        self.assertEqual(calls_page.status_code, 200)
        self.assertIn("Телефония и записи".encode("utf-8"), calls_page.data)
        self.assertIn(b'id="callsEmployeesBody"', calls_page.data)
        self.assertNotIn("Детальный реестр".encode("utf-8"), calls_page.data)
        self.assertNotIn("Тест записей".encode("utf-8"), calls_page.data)
        recordings_page = self.client.get("/call-recordings-demo")
        self.assertEqual(recordings_page.status_code, 200)
        self.assertIn("Записи звонков по противопоказаниям".encode("utf-8"), recordings_page.data)
        self.assertIn(b'id="recordingsRows"', recordings_page.data)
        self.assertIn(b"/api/call-recordings-demo", recordings_page.data)
        self.assertIn("без сохранения аудио".encode("utf-8"), recordings_page.data)
        funnel_page = self.client.get("/funnel")
        self.assertEqual(funnel_page.status_code, 200)
        self.assertIn("Сквозная аналитика".encode("utf-8"), funnel_page.data)
        self.assertIn("Состоявшиеся визиты".encode("utf-8"), funnel_page.data)
        self.assertIn(b'id="kpiRoi"', funnel_page.data)
        self.assertIn(b'id="funnelRows"', funnel_page.data)
        self.assertIn("без себестоимости".encode("utf-8"), funnel_page.data)

    def test_yclients_page_and_api_with_sample_mart(self):
        self.assertEqual(self.login().status_code, 302)

        class FakeYclientsAnalytics:
            def filters(self):
                return {
                    "branch": ["Свиблово"],
                    "direction": ["Массаж", "Лазер"],
                    "min_date": "2023-01-01",
                    "max_date": "2026-08-16",
                    "last_sync": "16.08.2026 13:45",
                }

            def report(self, *_args, **_kwargs):
                return {
                    "kpi": {"revenue": 1000.0, "visits": 1},
                    "groups": [],
                    "details": {"visits": [], "transactions": []},
                    "quality": {"unknown_visits": 0, "unknown_revenue": 0.0},
                    "method": {},
                }

            def color_reconciliation(self, *_args, **_kwargs):
                return {
                    "totals": {
                        "visits": 1,
                        "first_visits": 1,
                        "one_off_visits": 0,
                        "repeat_visits": 0,
                        "classified_total": 1,
                        "is_balanced": True,
                    },
                    "colors": [],
                    "new_colors": [],
                    "rows": [],
                    "pagination": {"page": 1, "per_page": 50, "pages": 1, "total": 0},
                }

            def last_sync(self):
                return "16.08.2026 13:45"

        self.app.extensions["yclients_analytics"] = FakeYclientsAnalytics()
        page = self.client.get("/yclients")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Yclients: визиты и выручка".encode("utf-8"), page.data)
        self.assertIn("Сверка дебиторки".encode("utf-8"), page.data)
        self.assertIn(b'<details class="yclients-debt-tray" id="installmentDebtTray">', page.data)
        self.assertNotIn(b'<details class="yclients-debt-tray" id="installmentDebtTray" open>', page.data)
        self.assertIn(b'id="installmentDebtRows"', page.data)
        self.assertIn(b'id="colorReconciliationTray"', page.data)
        self.assertNotIn(b'id="colorReconciliationTray" open', page.data)
        self.assertIn(b"/api/yclients/colors", page.data)
        response = self.client.get(
            "/api/yclients?date_from=2026-08-01&date_to=2026-08-16"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["kpi"]["revenue"], 1000.0)
        colors = self.client.get(
            "/api/yclients/colors?date_from=2026-08-01&date_to=2026-08-16"
        )
        self.assertEqual(colors.status_code, 200)
        self.assertEqual(colors.json["totals"]["visits"], 1)
        color_export = self.client.get(
            "/api/yclients/colors/export.csv?date_from=2026-08-01&date_to=2026-08-16"
        )
        self.assertEqual(color_export.status_code, 200)
        self.assertIn("text/csv", color_export.content_type)

    def test_calls_api_uses_selected_team(self):
        self.assertEqual(self.login().status_code, 302)

        class FakeCallAnalytics:
            def report(self, start, end, *, team, city, user):
                return {
                    "team": team,
                    "period": {"from": start.isoformat(), "to": end.isoformat()},
                    "kpi": {"calls": 10, "bookings": 2},
                    "employees": [], "daily": [],
                    "options": {"cities": [], "users": []},
                    "method": {"window_days": 7, "booking_label": "Все записи", "stages": []},
                }

        self.app.extensions["call_analytics"] = FakeCallAnalytics()
        self.app.extensions["dashboard_analytics"].last_sync = lambda: "2026-08-13 12:00"
        response = self.client.get(
            "/api/calls?date_from=2026-08-01&date_to=2026-08-13&team=admin&city=СПБ"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["team"], "admin")
        self.assertEqual(response.json["kpi"]["bookings"], 2)

    def test_call_recording_demo_list_and_audio_are_protected(self):
        self.assertEqual(self.login().status_code, 302)

        class FakeAudioResponse:
            status_code = 206
            headers = {
                "Content-Type": "audio/mpeg",
                "Content-Length": "3",
                "Content-Range": "bytes 0-2/3",
                "Accept-Ranges": "bytes",
            }

            def iter_content(self, chunk_size):
                yield b"mp3"

            def close(self):
                pass

        class FakeCallRecordingDemo:
            def list_calls(self):
                return {
                    "calls": [{
                        "note_id": 10,
                        "lead_id": 20,
                        "lead_name": "Тестовая сделка",
                        "lead_url": "https://example.amocrm.ru/leads/detail/20",
                        "occurred_at": "14.08.2026, 15:51",
                        "closed_at": "14.08.2026, 16:00",
                        "direction": "Исходящий",
                        "duration_sec": 180,
                        "provider": "UIS",
                        "employee": "Колл-центр Москва",
                        "phone": "••• ••• 12-34",
                        "loss_reason": "Противопоказание",
                        "audio_url": "/api/call-recordings-demo/10/audio",
                    }],
                    "shown": 1,
                    "eligible_calls": 9,
                    "eligible_leads": 4,
                    "provider": "UIS",
                    "loss_reason": "Противопоказание",
                    "method": "Пять самых продолжительных разговоров UIS",
                }

            def open_audio(self, note_id, range_header=None):
                self.note_id = note_id
                self.range_header = range_header
                return FakeAudioResponse()

        demo = FakeCallRecordingDemo()
        self.app.extensions["call_recordings_demo"] = demo
        response = self.client.get("/api/call-recordings-demo")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["calls"][0]["lead_id"], 20)
        self.assertEqual(response.json["calls"][0]["loss_reason"], "Противопоказание")

        audio = self.client.get(
            "/api/call-recordings-demo/10/audio",
            headers={"Range": "bytes=0-2"},
        )
        self.assertEqual(audio.status_code, 206)
        self.assertEqual(audio.data, b"mp3")
        self.assertEqual(audio.headers["Content-Type"], "audio/mpeg")
        self.assertEqual(demo.note_id, 10)
        self.assertEqual(demo.range_header, "bytes=0-2")

    def test_expense_api_preview_and_write_are_protected(self):
        self.assertEqual(self.login().status_code, 302)
        page = self.client.get("/expenses")
        self.assertEqual(page.status_code, 200)
        with self.client.session_transaction() as current_session:
            csrf = current_session["expense_csrf"]

        class FakeExpenseRepository:
            def list_rates(self):
                return []

            def list_period_expenses(self, start, end):
                return []

            def add_rate(self, payload, *, user):
                return {"id": 1, **payload, "created_by": user}

            def add_period_expense(self, payload, *, branches, user):
                return {
                    "id": 2,
                    "source": payload["source"],
                    "branch": payload.get("branch"),
                    "direction": payload.get("direction"),
                    "period_from": payload["period_from"],
                    "period_to": payload["period_to"],
                    "amount": float(payload["amount"]),
                    "status": payload.get("status", "draft"),
                    "status_label": "Черновик",
                    "comment": payload.get("comment", ""),
                }

            def set_period_status(self, expense_id, status, *, user):
                return {"id": expense_id, "status": status, "created_by": user}

        self.app.extensions["marketing_expenses"] = FakeExpenseRepository()
        analytics = self.app.extensions["dashboard_analytics"]
        analytics.last_sync = lambda: "2026-08-11 12:00"
        analytics.records = lambda start, end: [
            LeadRecord(1, date(2026, 8, 1), "Свиблово", "Массаж", "A", "VK", False),
            LeadRecord(2, date(2026, 8, 2), "Свиблово", "Массаж", "B", "VK", True),
        ]
        analytics.marketing_tag_records = lambda start, end: [
            MarketingTagRecord(
                1, date(2026, 8, 1), "Свиблово", "Массаж", "VK",
                "Свиблово", "Массаж", "VK",
            ),
            MarketingTagRecord(
                2, date(2026, 8, 2), "Свиблово", "Массаж", "VK",
                "Невский", "Массаж", "VK", "Дубль",
            ),
            MarketingTagRecord(
                3, date(2026, 8, 3), "Невский", "Массаж", "VK",
                "Свиблово", "Массаж", "VK",
            ),
        ]

        listing = self.client.get("/api/expenses?month=2026-08")
        self.assertEqual(listing.status_code, 200)
        self.assertIn("Свиблово", listing.json["branches"])
        self.assertTrue(listing.json["schema_ready"])

        payload = {
            "source": "VK",
            "branch": "Свиблово",
            "direction": "Массаж",
            "period_from": "2026-08-01",
            "period_to": "2026-08-31",
            "amount": "1000",
            "status": "draft",
        }
        self.assertEqual(
            self.client.post("/api/expenses/preview", json=payload).status_code,
            400,
        )
        headers = {"X-CSRF-Token": csrf}
        preview = self.client.post(
            "/api/expenses/preview", json=payload, headers=headers
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json["raw_leads"], 2)
        self.assertEqual(preview.json["excluded_leads"], 1)
        self.assertEqual(preview.json["matched_leads"], 1)
        self.assertEqual(preview.json["calculated_cpl"], 1000.0)
        created = self.client.post(
            "/api/expenses/periods", json=payload, headers=headers
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json["expense"]["preview"]["matched_leads"], 1)

        reconciliation = self.client.get(
            "/api/expenses/reconciliation?date_from=2026-08-01&date_to=2026-08-31"
            "&source=VK&branch=Свиблово&direction=Массаж"
        )
        self.assertEqual(reconciliation.status_code, 200)
        self.assertEqual(reconciliation.json["totals"]["tag_leads"], 2)
        self.assertEqual(reconciliation.json["totals"]["excluded_leads"], 1)
        export = self.client.get(
            "/api/expenses/reconciliation.csv?date_from=2026-08-01"
            "&date_to=2026-08-31&source=VK"
        )
        self.assertEqual(export.status_code, 200)
        self.assertIn("Всего по тегам".encode("utf-8"), export.data)

    def test_budget_api_lists_actuals_and_protects_writes(self):
        self.assertEqual(self.login().status_code, 302)
        self.client.get("/budgets")
        with self.client.session_transaction() as current_session:
            csrf = current_session["budget_csrf"]

        class FakeBudgetRepository:
            def list_budgets(self, month):
                return [
                    {
                        "id": 1,
                        "source": "РИС",
                        "branch": "Свиблово",
                        "direction": "Массаж",
                        "budget_month": "2026-08",
                        "amount": 10000,
                    }
                ]

            def save_budget(self, payload, *, branches, user):
                return {"id": 1, **payload, "updated_by": user}

            def update_budget(self, budget_id, payload, *, branches, user):
                return {"id": budget_id, **payload, "updated_by": user}

            def delete_budget(self, budget_id, *, user):
                return {"id": budget_id, "deleted": True, "updated_by": user}

        class FakeExpenseRepository:
            def list_rates(self):
                return [
                    {
                        "source": "РИС",
                        "direction": "Массаж",
                        "cost_per_lead": 500,
                        "valid_from": "2026-08-01",
                        "valid_to": None,
                    }
                ]

            def list_period_expenses(self, start, end):
                return []

        self.app.extensions["marketing_budgets"] = FakeBudgetRepository()
        self.app.extensions["marketing_expenses"] = FakeExpenseRepository()
        analytics = self.app.extensions["dashboard_analytics"]
        analytics.last_sync = lambda: "2026-08-11 12:00"
        analytics.records = lambda start, end: [
            LeadRecord(1, date(2026, 8, 2), "Свиблово", "Массаж", "A", "РИС", False)
        ]

        listing = self.client.get("/api/budgets?month=2026-08")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json["rows"][0]["spent"], 500.0)
        self.assertEqual(listing.json["rows"][0]["remaining"], 9500.0)
        self.assertEqual(listing.json["budgets"][0]["id"], 1)
        payload = {
            "source": "РИС",
            "branch": "Свиблово",
            "direction": "Массаж",
            "budget_month": "2026-08",
            "amount": "12000",
        }
        self.assertEqual(self.client.post("/api/budgets", json=payload).status_code, 400)
        saved = self.client.post(
            "/api/budgets",
            json=payload,
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(saved.status_code, 201)
        self.assertEqual(saved.json["budget"]["amount"], "12000")
        self.assertEqual(
            self.client.put("/api/budgets/1", json=payload).status_code,
            400,
        )
        updated = self.client.put(
            "/api/budgets/1",
            json={**payload, "amount": "13000"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json["budget"]["amount"], "13000")
        self.assertEqual(self.client.delete("/api/budgets/1").status_code, 400)
        deleted = self.client.delete(
            "/api/budgets/1",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.json["budget"]["deleted"])

    def test_dashboard_and_exports_with_sample_records(self):
        self.assertEqual(self.login().status_code, 302)
        analytics = self.app.extensions["dashboard_analytics"]
        records = [
            LeadRecord(1, date(2026, 7, 1), "Академическая", "Массаж", "A", "VK", True),
            LeadRecord(2, date(2026, 7, 2), "Академическая", "Массаж", "A", "VK", False),
        ]
        analytics.records = lambda start, end: records
        class FakeExpenseRepository:
            def list_rates(self):
                return []

            def list_period_expenses(self, start, end):
                return []

        self.app.extensions["marketing_expenses"] = FakeExpenseRepository()
        query = "?date_from=2026-07-01&date_to=2026-07-31"
        response = self.client.get("/api/dashboard" + query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["kpi"]["conversion"], 50.0)
        self.assertIn("marketing", response.json)
        self.assertEqual(response.json["marketing"]["kpi"]["leads"], 2)
        trend = self.client.get("/api/trends" + query + "&granularity=day")
        self.assertEqual(trend.status_code, 200)
        self.assertEqual(trend.json["current"]["summary"]["leads"], 2)
        self.assertEqual(len(trend.json["current"]["buckets"]), 31)
        self.assertNotIn("details", response.json)
        csv_response = self.client.get("/api/export.csv" + query)
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("text/csv", csv_response.content_type)
        xlsx_response = self.client.get("/api/export.xlsx" + query)
        self.assertEqual(xlsx_response.status_code, 200)
        self.assertGreater(len(xlsx_response.data), 1000)

    def test_verification_api_and_export(self):
        self.assertEqual(self.login().status_code, 302)
        analytics = self.app.extensions["dashboard_analytics"]
        sample = {
            "view": "bookings",
            "rows": [
                {
                    "lead_id": 10,
                    "client_phone": "+7 999 123-45-67",
                    "lead_name": "Тестовая сделка",
                    "lead_url": "",
                    "created_at": "2026-08-01T10:00+03:00",
                    "booking_date": "2026-08-02",
                    "branch": "Академическая",
                    "source": "VK",
                    "pipeline": "Москва",
                    "status": "Клиент записан",
                    "tags": ["ВК"],
                }
            ],
            "counts": {"leads": 1, "bookings": 1},
            "pagination": {
                "page": 1,
                "per_page": 50,
                "pages": 1,
                "total": 1,
            },
            "sort": {"key": "booking_date", "order": "desc"},
        }
        analytics.verification = lambda *args, **kwargs: sample
        query = "?view=bookings&date_from=2026-08-01&date_to=2026-08-31"
        response = self.client.get("/api/verification" + query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json["rows"][0]["booking_date"], "2026-08-02"
        )
        self.assertEqual(
            response.json["rows"][0]["client_phone"], "+7 999 123-45-67"
        )
        export = self.client.get("/api/verification/export.csv" + query)
        self.assertEqual(export.status_code, 200)
        self.assertIn("text/csv", export.content_type)
        self.assertIn("Дата записи".encode("utf-8"), export.data)
        self.assertIn("Телефон клиента".encode("utf-8"), export.data)
        self.assertIn("+7 999 123-45-67".encode("utf-8"), export.data)


if __name__ == "__main__":
    unittest.main()

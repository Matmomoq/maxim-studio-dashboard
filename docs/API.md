# Маршруты и API

Источник: `dashboard/app.py`; список снят из AST перед передачей.

| Метод | Адрес | Обработчик | Изменение состояния |
|---|---|---|---|
| GET, POST | `/login` | `login` | Да |
| POST | `/logout` | `logout` | Да |
| GET | `/` | `index` | Нет бизнес-записи* |
| GET | `/verification` | `verification_page` | Нет бизнес-записи* |
| GET | `/expenses` | `expenses_page` | Нет бизнес-записи* |
| GET | `/budgets` | `budgets_page` | Нет бизнес-записи* |
| GET | `/plans` | `plans_page` | Нет бизнес-записи* |
| GET | `/calls` | `calls_page` | Нет бизнес-записи* |
| GET | `/call-recordings-demo` | `call_recordings_demo_page` | Нет бизнес-записи* |
| GET | `/funnel` | `funnel_page` | Нет бизнес-записи* |
| GET | `/yclients` | `yclients_page` | Нет бизнес-записи* |
| GET | `/yclients/plan-fact` | `yclients_plan_fact_page` | Нет бизнес-записи* |
| GET | `/health` | `health` | Нет бизнес-записи* |
| GET | `/api/filters` | `filters` | Нет бизнес-записи* |
| GET | `/api/expenses` | `expenses_api` | Нет бизнес-записи* |
| GET | `/api/plans` | `plans_api` | Нет бизнес-записи* |
| POST | `/api/plans` | `plan_create_api` | Да |
| PUT | `/api/plans/<int:plan_id>` | `plan_update_api` | Да |
| DELETE | `/api/plans/<int:plan_id>` | `plan_delete_api` | Да |
| GET | `/api/plans/<int:plan_id>/history` | `plan_history_api` | Нет бизнес-записи* |
| POST | `/api/expenses/preview` | `expenses_preview_api` | Да |
| GET | `/api/expenses/reconciliation` | `expenses_reconciliation_api` | Нет бизнес-записи* |
| GET | `/api/expenses/reconciliation.csv` | `expenses_reconciliation_export` | Нет бизнес-записи* |
| POST | `/api/expenses/rates` | `expenses_rate_create_api` | Да |
| POST | `/api/expenses/periods` | `expenses_period_create_api` | Да |
| POST | `/api/expenses/periods/<int:expense_id>/<status>` | `expenses_period_status_api` | Да |
| GET | `/api/budgets` | `budgets_api` | Нет бизнес-записи* |
| GET | `/api/calls` | `calls_api` | Нет бизнес-записи* |
| GET | `/api/call-recordings-demo` | `call_recordings_demo_api` | Нет бизнес-записи* |
| GET | `/api/call-recordings-demo/<int:note_id>/audio` | `call_recording_demo_audio` | Нет бизнес-записи* |
| GET | `/api/funnel` | `funnel_api` | Нет бизнес-записи* |
| GET | `/api/yclients/filters` | `yclients_filters_api` | Нет бизнес-записи* |
| GET | `/api/yclients` | `yclients_api` | Нет бизнес-записи* |
| GET | `/api/yclients/plan-fact` | `yclients_plan_fact_api` | Да: синхронизация plan_fact_* |
| GET | `/api/yclients/colors` | `yclients_colors_api` | Нет бизнес-записи* |
| GET | `/api/yclients/colors/export.csv` | `yclients_colors_export_csv` | Нет бизнес-записи* |
| POST | `/api/budgets` | `budget_save_api` | Да |
| PUT | `/api/budgets/<int:budget_id>` | `budget_update_api` | Да |
| DELETE | `/api/budgets/<int:budget_id>` | `budget_delete_api` | Да |
| GET | `/api/dashboard` | `dashboard` | Нет бизнес-записи* |
| GET | `/api/trends` | `trends_api` | Нет бизнес-записи* |
| GET | `/api/verification` | `verification_api` | Нет бизнес-записи* |
| GET | `/api/verification/export.csv` | `verification_export_csv` | Нет бизнес-записи* |
| GET | `/api/export.csv` | `export_csv` | Нет бизнес-записи* |
| GET | `/api/export.xlsx` | `export_xlsx` | Нет бизнес-записи* |

* GET страниц может обновить cookie сессии/CSRF. Доступ к аудио может обновить OAuth-токены. GET `/api/yclients/plan-fact` синхронизирует события записи и замечания в `plan_fact_*`: это не чистое чтение.

API требует сессионную cookie. Записывающие API требуют `X-CSRF-Token` из соответствующей страницы; login/logout используют поле формы `csrf_token`. Параметры и тела запросов смотрите в обработчике и парном JS-файле. Общие параметры отчётов: `date_from`, `date_to`; месячных форм — `month=YYYY-MM`; списки фильтров передаются повторяющимися параметрами. Не все фильтры применимы ко всем разделам.

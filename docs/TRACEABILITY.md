# Трассировка требований → проверки

**Инкремент:** [001, M1](../specs/001-core-workflow/spec.md).  
**Состояние:** ядро и offline-адаптер реализованы; реальные Telegram/Google транспорты не реализованы.

Статус «ядро проверено» не означает приёмку всего пользовательского сценария через реальные API. Имена тестов ниже доступны в каталоге [`tests/`](../tests/).

## Продуктовые критерии

| Критерий | Проверка M1 | Что остаётся |
| --- | --- | --- |
| AC-01, запись | `test_ac01_first_free_letter_without_increment`, `test_public_zone_has_only_task_booking_buttons` | Telegram-клик, toast и редактирование сообщения — M2 |
| AC-02, подтверждение за известным человеком | `test_ac02_default_solver_is_known_reserver`, `test_private_admin_zone_contains_identity_and_current_indicators` | Реальная личная панель — M2 |
| AC-03, старт и баллы | `test_ac03_round_up_and_zero_guard`, `test_startup_exception_precedes_regular_colors`, property-based проверки | Проверено в ядре |
| AC-04, другой сдавший | `test_ac04_actual_solver_gets_credit_and_other_booking_is_released` | Клавиатура выбора и пагинация — M2 |
| AC-05, merge подпунктов | `test_ac05_merge_preserves_reservations_and_completions` | Парсер команды `/add` — M2 |
| AC-06, гонка за последнюю букву | `test_ac06_concurrent_clicks_only_one_wins` | Проверено в одном процессе; несколько процессов не поддерживаются |
| AC-07, старый Pull | `test_ac07_stale_pull_does_not_erase_unsent_increment` | Реальный Sheets-адаптер — M3 |
| AC-08, приоритет ручного итога | `test_ac08_new_manual_total_wins_without_double_increment` | Физическая схема и реальные API — M3; ограничения CAS сохраняются |
| AC-09, незавершённая бронь/очистка | `test_ac09_clear_preserves_counts_and_unsent_changes` | Административная команда — M2 |
| AC-10, нет одобренной привязки | `test_ac10_registration_requires_approval`, `test_ac10_unknown_click_has_registration_hint` | Доставка заявки и ответов — M2 |
| AC-11, все вне системы | `test_ac11_no_active_people`, `test_bootstrap_admin_can_account_when_everyone_is_outside` | Подключение реальной ведомости — M3 |
| AC-12, одна бронь | `test_ac12_one_reservation_per_person` | Проверено в ядре |
| AC-13, отмена своей | `test_ac13_cancel_own_reservation_does_not_change_counts` | Telegram-кнопка — M2 |
| AC-14, повтор подтверждения | `test_ac14_double_confirmation_is_idempotent` | Проверено в ядре; дедупликация повторно доставленного update — M2 |
| AC-15, без флуда | `test_ac23_duplicate_add_does_not_ping_or_restart_window`, `test_public_zone_has_only_task_booking_buttons` | Доставка одного события и Telegram rate limits — M2 |
| AC-16, сбой API без блокировки пары | `test_read_failure_preserves_local_state_and_records_failure`, `test_write_failure_survives_restart_and_retries`, `test_network_read_does_not_hold_state_lock` | Реальные timeout/backoff/воркеры — M3 |
| AC-17, административные права | `test_ac17_non_admin_cannot_confirm_or_clear`, `test_unprivileged_user_cannot_obtain_admin_snapshot`, `test_manual_role_revocation_is_not_overridden_by_bootstrap` | Проверка разрешённого чата в transport layer — M2 |
| AC-18, старая сессия | `test_ac18_stale_callbacks_cannot_change_new_session` | Кодирование callback_data и проверка лимита Telegram — M2 |
| AC-19, окно 20 секунд | `test_ac19_window_blocks_yellow_and_red`, `test_planned_red_still_waits_for_green_window` | Реальное обновление табло по времени — M2 |
| AC-20, некого ждать | `test_ac20_window_skips_when_all_green_already_booked`, `test_window_skips_unregistered_green_but_they_remain_in_average` | Проверено в ядре |
| AC-21, внесистемный без TG | `test_ac21_outside_without_telegram_can_submit` | Кнопки полного списка ФИО — M2 |
| AC-22, две заявки на одно ФИО | `test_ac22_two_claims_one_name_only_one_approval` | Личные сообщения ответственным — M2 |
| AC-23, повтор добавления | `test_ac23_duplicate_add_does_not_ping_or_restart_window` | Реальный бот-событийный outbox — M2 |

## Дополнительные гарантии инкремента

| Контракт | Проверки |
| --- | --- |
| C-01, расчёты и строгие данные | `test_engine.py`, модели и защитные проверки в `test_storage.py`/`test_guards.py` |
| C-02, регистрация и права | Заявка/одобрение/отказ, отказ в чужих полномочиях, конкурирующие одобрения |
| C-03, задачи и бронь | Merge, окна, сброс анти-тишины, отмена, одна бронь, конкурентный последний подпункт |
| C-04, факт сдачи | Сдача за бронировавшим/другим/внесистемным, идемпотентность, очистка и старая сессия |
| C-05, сохранение | `test_atomic_replace_failure_preserves_previous_file`, `test_failed_repository_save_does_not_publish_memory_change`, `test_cancelled_caller_cannot_separate_disk_and_memory`, `test_corrupt_state_is_not_silently_reset` |
| C-06, согласование | `test_new_local_revision_during_write_is_not_cleared`, `test_uncertain_write_and_new_revision_are_recognized_after_restart`, `test_missing_row_does_not_delete_person_or_erase_pending` |
| C-07, разделение зон | `test_views.py`, `test_pending_registration_is_visible_only_in_admin_view`, экранирование ФИО |
| Offline-исполняемый сценарий | `test_offline_vertical_slice` и `python -m algem_bot.demo` |
| Запрет случайного сетевого доступа | Автоматическая fixture `no_network`, `test_external_connections_are_blocked_by_default` |

## Ограничения покрытия

Покрытие coverage.py относится к исходникам ядра с учётом ветвлений; CLI-демо исключено из процента, но отдельно проходит smoke-тест и ручной запуск. Процент покрытия не доказывает корректность реального Google API или доставки сообщений Telegram.

Утверждения о проверках и их окружении: [evidence](../specs/001-core-workflow/evidence.md).

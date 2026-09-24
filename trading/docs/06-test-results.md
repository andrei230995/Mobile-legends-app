# Test results

Run on 2026-09-24 in the build container (Python 3.11.15, Linux): `cd trading/backend && python -m pytest -q` → **69 passed**. CI (`.github/workflows/trading-tests.yml`) runs the same tests, pyflakes, the bundled validation, and a Docker build and boot check.

Also verified by hand in the build container:
* Docker image built and ran as a non-root user; `/healthz` returned 200; state persisted across `docker restart` (a local-only Dockerfile variant was needed to trust the sandbox's TLS proxy CA; the committed Dockerfile is unchanged).
* Web app driven with Chromium at a 390×844 iPhone viewport: login, all six tabs, pause/resume controls, live activation refused for a wrong password and then throttled; no JavaScript errors apart from one missing favicon (since added).
* 25-day replay (Feb 2018 sell-off, $1,300 simulated account): entries, broker-held stops, gap-through stop fills, strategy exit, re-entries; daily GBP decomposition with a £0.00 residual.

## Coverage of the required failure cases

| Required case | Tests (`trading/backend/tests/`) |
|---|---|
| Stale or missing prices | `test_stale_quote_blocks_entry_and_is_retried`, `test_missing_prices_block_entry`, `test_data_outage_with_software_stop_exits_after_grace`, `test_alpaca_data_quote_and_bars_exclude_incomplete_session` |
| Insufficient cash / invalid sizes / £10 | `test_ten_pound_account_sizes_or_explains`, `test_ten_pound_account_can_trade_when_limits_allow`, `test_order_below_broker_minimum_is_impossible_with_explanation`, `test_alpaca_account_never_uses_margin_buying_power`, `test_regulatory_fee_cent_rounding_hits_small_orders` |
| Partial fills and rejected orders | `test_partial_fill_then_ttl_cancel_and_stop_sized_to_fill`, `test_rejected_order_recorded_and_notified`, `test_alpaca_submit_error_classification` |
| API timeouts and rate limits | `test_timeout_after_accept_reconciles_without_duplicate`, `test_timeout_before_accept_waits_for_grace_then_marks_not_sent`, `test_rate_limit_is_not_sent_and_safe_to_retry`, `test_alpaca_timeout_on_submit_is_unknown_but_on_read_is_unavailable`, `test_alpaca_connect_error_on_submit_is_safe_not_sent` |
| Duplicate submissions | `test_duplicate_submission_suppressed`, `test_one_entry_attempt_per_session_and_idempotent_ids`, `test_alpaca_lookup_by_client_id_and_missing`, `test_t212_reconciles_without_client_id_by_matching` |
| Restart with pending orders | `test_restart_with_unknown_order_reconciles`, `test_crash_between_write_and_send_is_resolved` |
| Reconciliation discrepancies | `test_reconciliation_mismatch_blocks_entries` |
| Risk-limit breaches | `test_daily_loss_limit_pauses_entries_but_keeps_managing`, `test_drawdown_flatten_policy`, `test_pause_does_not_disable_protective_management`, `test_hard_ceilings_cannot_be_configured`, `test_loosening_limits_needs_password_tightening_does_not` |
| Credential failure and lost connectivity | `test_credential_failure_halts`, `test_connectivity_loss_alerts_after_three_failures_and_recovers`, `test_t212_auth_error` |
| Accurate P&L with deposits, withdrawals, FX | `test_fifo_realised_in_gbp_uses_fill_fx_and_fees`, `test_deposit_is_not_profit_and_decomposition_adds_up`, `test_open_losing_position_is_reported`, `test_nav_units_ignore_deposits_for_drawdown` |
| Full autonomous cycle, modes, controls | `test_full_cycle_entry_stop_exit`, `test_no_entries_outside_window_or_when_closed`, `test_read_only_mode_never_sends_orders`, `test_cancel_pending_entries_control`, `test_software_stop_when_broker_cannot_hold_stop` |
| Security / live gating | `test_requires_login`, `test_wrong_password_and_throttle`, `test_csrf_required_for_state_changes`, `test_live_activation_blocked_with_reasons`, `test_live_activation_requires_password`, `test_strategy_cannot_be_approved_when_gate_fails`, `test_no_secrets_in_state`, `test_secrets_not_in_repr`, `test_security_headers_and_health` |
| AI containment | `test_untrusted_text_is_delimited_and_escaped`, `test_malformed_or_extra_fields_are_discarded`, `test_refusal_is_not_an_assessment`, `test_veto_only_in_veto_mode_and_only_blocks`, `test_budget_cap` |
| Backtest integrity | `test_orders_fill_next_open_not_signal_close`, `test_delay_shifts_execution`, `test_costs_reduce_returns_monotonically`, `test_gap_through_stop_fills_at_open_not_stop` |
| Calendars / DST | `test_calendar_dst_and_holidays`, `test_report_time_is_uk_local_across_bst` |

Broker adapters are tested against **mocked HTTP responses written from the documented formats**. They have not been run against Alpaca's or Trading 212's servers (see [capability status](07-capability-status.md)).

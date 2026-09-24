"""P&L accuracy with deposits, withdrawals, fees and FX."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tradebot.accounting import Ledger, Nav, daily_report
from tradebot.db import Store, iso

T0 = datetime(2026, 3, 2, 21, 30, tzinfo=timezone.utc)


def fill(st, fid, side, qty, px, fee, fx, ts, sym="SPY"):
    st.execute("INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (fid, fid, sym, side, str(qty), str(px), "USD", str(fee), str(fx), "test", iso(ts), "entry", "s:SPY"))


def snap(st, ts, equity_usd, cash_usd, fx, unreal_gbp):
    st.execute("INSERT INTO equity_snapshots VALUES(?,?,?,?,?,?,?)",
               (iso(ts), "USD", str(equity_usd), str(cash_usd), str(fx), str(Decimal(equity_usd) * Decimal(fx)),
                str(unreal_gbp)))


def flow(st, fid, ts, kind, amount, fx):
    st.execute("INSERT INTO cash_flows VALUES(?,?,?,?,?,?,?)", (fid, iso(ts), kind, str(amount), "USD", str(fx), "t"))


def test_fifo_realised_in_gbp_uses_fill_fx_and_fees():
    st = Store(":memory:")
    fill(st, "b1", "buy", 2, 100, 0, "0.80", T0)
    fill(st, "b2", "buy", 2, 110, 0, "0.80", T0 + timedelta(minutes=1))
    fill(st, "s1", "sell", 3, 120, "0.02", "0.75", T0 + timedelta(hours=1))
    _, realised, _ = Ledger(st).build()
    r = realised[0]
    # cost: 2*100*0.8 + 1*110*0.8 = 248 GBP; proceeds (360-0.02)*0.75 = 269.985
    assert r.realised_gbp == Decimal("269.985") - Decimal("248")
    assert r.realised_ccy == Decimal("359.98") - Decimal("310")
    assert Ledger(st).open_qty() == {"SPY": Decimal("1")}
    unreal, per = Ledger(st).unrealised_gbp({"SPY": Decimal("105")}, Decimal("0.75"))
    assert unreal == Decimal("105") * Decimal("0.75") - Decimal("88")    # remaining lot cost 110*0.8


def test_deposit_is_not_profit_and_decomposition_adds_up():
    st = Store(":memory:")
    fx0, fx1 = Decimal("0.80"), Decimal("0.75")
    snap(st, T0, 1000, 1000, fx0, 0)                              # £800
    t = T0 + timedelta(hours=16)
    fill(st, "b1", "buy", 1, 500, 0, fx1, t)
    fill(st, "s1", "sell", 1, 510, "0.02", fx1, t + timedelta(hours=1))
    flow(st, "d1", t + timedelta(hours=2), "deposit", 200, fx1)
    flow(st, "w1", t + timedelta(hours=3), "withdrawal", -50, fx1)
    end_equity = Decimal(1000) + Decimal("9.98") + 200 - 50       # USD
    snap(st, T0 + timedelta(days=1), end_equity, end_equity, fx1, 0)
    rep = daily_report(st, "2026-03-03", T0, T0 + timedelta(days=1), Decimal("0.15"), fx1)
    assert rep["deposits_withdrawals_gbp"] == "112.50"             # 150 USD * 0.75
    total = Decimal(rep["trading_pnl_gbp"])
    parts = (Decimal(rep["realised_pnl_gbp"]) + Decimal(rep["unrealised_change_gbp"])
             + Decimal(rep["dividends_interest_cash_fees_gbp"]) + Decimal(rep["fx_and_other_residual_gbp"]))
    assert total == parts                          # displayed figures add up exactly
    # USD cash lost value in GBP (0.80 -> 0.75): shown as FX residual, not hidden
    assert Decimal(rep["fx_and_other_residual_gbp"]) < 0
    assert rep["realised_pnl_gbp"] == "7.49"      # (509.98 - 500) * 0.75 = 7.485, half-up
    assert rep["net_after_operating_costs_gbp"] == str((total - Decimal("0.15")).quantize(Decimal("0.01")))


def test_open_losing_position_is_reported():
    st = Store(":memory:")
    snap(st, T0, 1000, 1000, 1, 0)
    snap(st, T0 + timedelta(days=1), 900, 500, 1, -100)
    rep = daily_report(st, "d", T0, T0 + timedelta(days=1), Decimal(0), Decimal(1))
    assert rep["trading_pnl_gbp"] == "-100.00" and rep["unrealised_change_gbp"] == "-100.00"


def test_nav_units_ignore_deposits_for_drawdown():
    st = Store(":memory:")
    nav = Nav(st)
    nav.update(Decimal(100))
    nav.update(Decimal(90))                      # -10%
    _, dd = nav.update(Decimal(90))
    assert abs(dd - Decimal(10)) < Decimal("1e-20")
    nav.apply_flow(Decimal(910), Decimal(90))    # deposit £910 -> equity £1000
    _, dd = nav.update(Decimal(1000))
    assert abs(dd - Decimal(10)) < Decimal("1e-20")   # deposit neither hid nor worsened the drawdown
    nav.apply_flow(Decimal(-500), Decimal(1000))
    _, dd = nav.update(Decimal(500))
    assert abs(dd - Decimal(10)) < Decimal("1e-20")

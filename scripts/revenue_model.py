#!/usr/bin/env python3
"""Bottom-up revenue model for PBSdata.io.

Not a projection — a way to see which assumptions actually move the outcome.
Every number here is an input you should argue with. Run it, change the
scenario dicts, and watch break-even move.

    python scripts/revenue_model.py
    python scripts/revenue_model.py --scenario base --months 36

All figures AUD, ex-GST.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Addressable market — counted bottom-up, by naming the buyers.
# "Reachable" means: a solo operator can plausibly get a meeting AND they can
# plausibly buy from a one-person company (which rules out most hospital and
# health-fund procurement).
# --------------------------------------------------------------------------

# Counts for the first three rows are ENUMERATED, not estimated: WA Health's
# "Electronic prescribing and dispensing software approved in WA" (D00121.31,
# updated 23 Dec 2025) lists every approved product by name. Vendors seek WA
# approval as a matter of course, so it is the best public census available.
# Products are collapsed to vendors (Fred Classic + Plus = 1; Corum LOTS +
# Clear Dispense = 1; Genie + Gentu + VIP.net = 1; MedicalDirector + Helix = 1).
# Remaining rows are estimates and are flagged as such in the plan.
SEGMENTS = [
    # name,                                total, reachable, line, sourced
    ("Dispensing / pharmacy software",         9,         6, "api", True),
    ("Prescribing / EMR / clinic software",   21,        10, "api", True),
    ("Aged care eNRMC / medication charts",    8,         5, "api", True),
    ("Telehealth & digital health (other)",   20,        12, "api", False),
    ("Med management / adherence / AI",       20,        12, "api", False),
    ("Hospital pharmacy systems",              8,         2, "api", False),
    ("Health funds / insurers",                8,         1, "api", False),
    ("Pharma market access (AU)",             50,        22, "mi", False),
    ("Consultancies / HTA / research",        25,        14, "mi", False),
]


@dataclass
class Line:
    """One product line's funnel and economics."""

    name: str
    reachable: int
    arpa: float                  # blended monthly revenue per account
    accounts_worked_pm: float    # new accounts you can genuinely work per month
    contact_to_meeting: float
    meeting_to_pilot: float
    pilot_to_paid: float
    pilot_months: int            # lag between pilot start and first invoice
    monthly_churn: float
    inbound_pm_y1: float = 0.0   # extra qualified accounts/month from inbound
    inbound_pm_y3: float = 0.0
    start_month: int = 1         # when you start selling this line
    annual_prepay: bool = False
    # In a market this small the list runs out in under a year. Survival
    # depends on re-working the "no"s — their self-built pipeline drifts, an
    # engineer leaves, a bad join reaches production. A no is a not-yet.
    recycle_months: int = 12
    recycle_uplift: float = 1.3  # warm second touch converts better than cold


@dataclass
class Scenario:
    name: str
    lines: list[Line]
    # fixed monthly costs, ramped y1 -> y3
    hosting_y1: float = 150
    hosting_y3: float = 450
    insurance: float = 260       # professional indemnity + public liability
    admin: float = 250           # accounting, ASIC, company
    tooling: float = 90          # domain, email, monitoring, misc SaaS
    marketing_y1: float = 400
    marketing_y3: float = 1400
    payment_fee_pct: float = 0.02
    onetime: dict[int, tuple[str, float]] = field(default_factory=dict)


def _ramp(y1: float, y3: float, month: int) -> float:
    """Linear ramp from the y1 value to the y3 value across 36 months."""
    t = min(max(month - 1, 0), 35) / 35
    return y1 + (y3 - y1) * t


def build_scenarios() -> dict[str, Scenario]:
    api_reachable = sum(r for _, _, r, ln, _s in SEGMENTS if ln == "api")
    mi_reachable = sum(r for _, _, r, ln, _s in SEGMENTS if ln == "mi")

    def onetime_costs(legal: float) -> dict[int, tuple[str, float]]:
        return {
            1: ("Legal: licensing advice, T&Cs, contract templates", legal),
            2: ("Domain, company setup, brand", 1200.0),
            13: ("Annual legal / contract review", 2000.0),
            25: ("Annual legal / contract review", 2000.0),
        }

    return {
        # ------------------------------------------------------------------
        "bear": Scenario(
            name="Bear — the market says no",
            lines=[
                Line(
                    "API subscriptions", api_reachable, arpa=780,
                    accounts_worked_pm=5, contact_to_meeting=0.22,
                    meeting_to_pilot=0.30, pilot_to_paid=0.35,
                    pilot_months=3, monthly_churn=0.025,
                    inbound_pm_y1=0.2, inbound_pm_y3=0.8,
                ),
                Line(
                    "Market intelligence", mi_reachable, arpa=2200,
                    accounts_worked_pm=2, contact_to_meeting=0.15,
                    meeting_to_pilot=0.25, pilot_to_paid=0.30,
                    pilot_months=5, monthly_churn=0.020,
                    start_month=13, annual_prepay=True,
                ),
            ],
            onetime=onetime_costs(5000.0),
        ),
        # ------------------------------------------------------------------
        "base": Scenario(
            name="Base — correctness pitch lands with a minority",
            lines=[
                Line(
                    "API subscriptions", api_reachable, arpa=1150,
                    accounts_worked_pm=8, contact_to_meeting=0.30,
                    meeting_to_pilot=0.38, pilot_to_paid=0.50,
                    pilot_months=3, monthly_churn=0.015,
                    inbound_pm_y1=0.4, inbound_pm_y3=2.0,
                ),
                Line(
                    "Market intelligence", mi_reachable, arpa=3000,
                    accounts_worked_pm=3, contact_to_meeting=0.22,
                    meeting_to_pilot=0.35, pilot_to_paid=0.45,
                    pilot_months=4, monthly_churn=0.012,
                    inbound_pm_y1=0.1, inbound_pm_y3=0.6,
                    start_month=10, annual_prepay=True,
                ),
            ],
            onetime=onetime_costs(6000.0),
        ),
        # ------------------------------------------------------------------
        "bull": Scenario(
            name="Bull — one anchor vendor validates you publicly",
            lines=[
                Line(
                    "API subscriptions", api_reachable, arpa=1650,
                    accounts_worked_pm=10, contact_to_meeting=0.38,
                    meeting_to_pilot=0.45, pilot_to_paid=0.60,
                    pilot_months=2, monthly_churn=0.010,
                    inbound_pm_y1=0.8, inbound_pm_y3=4.0,
                ),
                Line(
                    "Market intelligence", mi_reachable, arpa=3800,
                    accounts_worked_pm=4, contact_to_meeting=0.30,
                    meeting_to_pilot=0.45, pilot_to_paid=0.55,
                    pilot_months=3, monthly_churn=0.008,
                    inbound_pm_y1=0.3, inbound_pm_y3=1.5,
                    start_month=7, annual_prepay=True,
                ),
            ],
            onetime=onetime_costs(7000.0),
        ),
    }


@dataclass
class MonthRow:
    month: int
    customers: dict[str, float]
    mrr: float
    revenue: float          # cash recognised this month
    costs: float
    net: float
    cum_net: float


def run(sc: Scenario, months: int = 36) -> list[MonthRow]:
    customers = {ln.name: 0.0 for ln in sc.lines}
    # accounts never yet approached
    cold = {ln.name: float(ln.reachable) for ln in sc.lines}
    # approached, didn't buy — eligible to re-work after recycle_months
    reheat: dict[str, dict[int, float]] = {ln.name: {} for ln in sc.lines}
    warm = {ln.name: 0.0 for ln in sc.lines}
    # pilots[line][month_they_convert] = number of pilots landing then
    pilots: dict[str, dict[int, float]] = {ln.name: {} for ln in sc.lines}

    rows: list[MonthRow] = []
    cum_net = 0.0

    for m in range(1, months + 1):
        mrr = 0.0
        for ln in sc.lines:
            if m >= ln.start_month:
                # --- accounts whose cooling-off period has elapsed come back
                warm[ln.name] += reheat[ln.name].pop(m, 0.0)

                # --- top of funnel: cold list first, then re-work the "no"s
                capacity = ln.accounts_worked_pm
                worked_cold = min(capacity, cold[ln.name])
                cold[ln.name] -= worked_cold
                worked_warm = min(capacity - worked_cold, warm[ln.name])
                warm[ln.name] -= worked_warm

                inbound = _ramp(ln.inbound_pm_y1, ln.inbound_pm_y3, m)

                # a warm second touch converts better than a cold first one
                effective = (
                    worked_cold
                    + worked_warm * ln.recycle_uplift
                    + inbound * ln.recycle_uplift
                )

                # --- funnel conversion, with the pilot lag
                rate = ln.contact_to_meeting * ln.meeting_to_pilot
                new_pilots = effective * rate
                land = m + ln.pilot_months
                pilots[ln.name][land] = pilots[ln.name].get(land, 0.0) + new_pilots

                # --- those who won't convert cool off, then come back around
                approached = worked_cold + worked_warm
                lost = approached * (1 - rate * ln.pilot_to_paid)
                back = m + ln.recycle_months
                reheat[ln.name][back] = reheat[ln.name].get(back, 0.0) + lost

                # --- pilots converting this month
                won = pilots[ln.name].pop(m, 0.0) * ln.pilot_to_paid
                # you cannot sell to more accounts than exist
                customers[ln.name] = min(customers[ln.name] + won, ln.reachable)

            # --- churn
            customers[ln.name] *= 1 - ln.monthly_churn
            mrr += customers[ln.name] * ln.arpa

        # ------------------------------------------------------------------
        revenue = mrr
        costs = (
            _ramp(sc.hosting_y1, sc.hosting_y3, m)
            + sc.insurance
            + sc.admin
            + sc.tooling
            + _ramp(sc.marketing_y1, sc.marketing_y3, m)
            + revenue * sc.payment_fee_pct
        )
        if m in sc.onetime:
            costs += sc.onetime[m][1]

        net = revenue - costs
        cum_net += net
        rows.append(
            MonthRow(m, dict(customers), mrr, revenue, costs, net, cum_net)
        )

    return rows


def report(sc: Scenario, rows: list[MonthRow]) -> None:
    print(f"\n{'=' * 78}\n{sc.name}\n{'=' * 78}")

    hdr = f"{'Mo':>3} " + "".join(f"{ln.name[:14]:>16}" for ln in sc.lines)
    print(f"{hdr} {'MRR':>10} {'Costs':>9} {'Net':>10} {'Cum':>11}")
    for r in rows:
        if r.month % 3 and r.month != 1:
            continue
        cells = "".join(f"{r.customers[ln.name]:>16.1f}" for ln in sc.lines)
        print(
            f"{r.month:>3} {cells} {r.mrr:>10,.0f} {r.costs:>9,.0f} "
            f"{r.net:>10,.0f} {r.cum_net:>11,.0f}"
        )

    print(f"\n{'Year':<6}{'Revenue':>12}{'Costs':>12}{'Net':>12}{'Exit MRR':>12}"
          f"{'Exit ARR':>12}{'Customers':>11}")
    for y in range(1, len(rows) // 12 + 1):
        chunk = rows[(y - 1) * 12: y * 12]
        rev = sum(r.revenue for r in chunk)
        cost = sum(r.costs for r in chunk)
        last = chunk[-1]
        print(
            f"Y{y:<5}{rev:>12,.0f}{cost:>12,.0f}{rev - cost:>12,.0f}"
            f"{last.mrr:>12,.0f}{last.mrr * 12:>12,.0f}"
            f"{sum(last.customers.values()):>11.1f}"
        )

    be = next((r.month for r in rows if r.net > 0), None)
    cb = next((r.month for r in rows if r.cum_net > 0), None)
    trough = min(rows, key=lambda r: r.cum_net)
    print(f"\n  Monthly break-even : {f'month {be}' if be else 'not reached'}")
    print(f"  Cumulative payback : {f'month {cb}' if cb else 'not reached'}")
    print(f"  Peak cash needed   : {abs(min(trough.cum_net, 0)):,.0f} "
          f"(month {trough.month})")

    # Opportunity cost is the number that actually decides this.
    contractor_rate = 175_000
    yrs = len(rows) / 12
    print(f"  Forgone salary     : {contractor_rate * yrs:,.0f} "
          f"over {yrs:.0f}y at {contractor_rate:,}/y")
    print(f"  Cumulative net     : {rows[-1].cum_net:,.0f}")
    verdict = "beats" if rows[-1].cum_net > contractor_rate * yrs else "LOSES TO"
    print(f"  => {verdict} taking a job\n")


def market_table() -> None:
    print(f"\n{'=' * 78}\nAddressable market, counted bottom-up\n{'=' * 78}")
    print(f"{'Segment':<38}{'Total':>8}{'Reachable':>11}{'Line':>8}{'Source':>9}")
    for name, total, reach, line, sourced in SEGMENTS:
        tag = "counted" if sourced else "est."
        print(f"{name:<38}{total:>8}{reach:>11}{line:>8}{tag:>9}")
    ta = sum(t for _, t, _, _, _ in SEGMENTS)
    tr = sum(r for _, _, r, _, _ in SEGMENTS)
    print(f"{'TOTAL':<38}{ta:>8}{tr:>11}")
    print(f"\n  Every paying customer is ~{100 / tr:.1f}% of the reachable market.")
    print("  There is no long tail. This is an account-by-account business.")


def sensitivity(months: int = 36) -> None:
    """Which single assumption most changes the answer? Vary each +/-30%."""
    import copy

    knobs = [
        ("ARPA (pricing power)", "arpa"),
        ("Contact -> meeting rate", "contact_to_meeting"),
        ("Meeting -> pilot rate", "meeting_to_pilot"),
        ("Pilot -> paid rate", "pilot_to_paid"),
        ("Accounts worked / month", "accounts_worked_pm"),
        ("Inbound (year 3)", "inbound_pm_y3"),
        ("Monthly churn", "monthly_churn"),
        ("Reachable market size", "reachable"),
        ("Recycle uplift", "recycle_uplift"),
    ]

    baseline = build_scenarios()["base"]
    base_cum = run(baseline, months)[-1].cum_net

    print(f"\n{'=' * 78}\nSensitivity — 36-month cumulative net, base case "
          f"({base_cum:,.0f})\n{'=' * 78}")
    print(f"{'Assumption':<28}{'-30%':>14}{'+30%':>14}{'Swing':>14}")

    results = []
    for label, attr in knobs:
        outs = []
        for mult in (0.7, 1.3):
            sc = copy.deepcopy(baseline)
            for ln in sc.lines:
                setattr(ln, attr, getattr(ln, attr) * mult)
            outs.append(run(sc, months)[-1].cum_net)
        results.append((label, outs[0], outs[1], abs(outs[1] - outs[0])))

    for label, lo, hi, swing in sorted(results, key=lambda r: -r[3]):
        print(f"{label:<28}{lo:>14,.0f}{hi:>14,.0f}{swing:>14,.0f}")

    print("\n  Read the top row as: this is the thing to go and learn.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="all",
                    choices=["all", "bear", "base", "bull"])
    ap.add_argument("--months", type=int, default=36)
    ap.add_argument("--no-sensitivity", action="store_true")
    args = ap.parse_args()

    market_table()
    scenarios = build_scenarios()
    picked = scenarios if args.scenario == "all" else {
        args.scenario: scenarios[args.scenario]
    }
    for sc in picked.values():
        report(sc, run(sc, args.months))

    if not args.no_sensitivity:
        sensitivity(args.months)


if __name__ == "__main__":
    main()

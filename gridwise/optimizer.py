import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from gridwise.models import Directive, Plan, PlanHour, Scenario


class InfeasibleError(Exception):
    pass


def optimize(scenario: Scenario, directives: list[Directive]) -> Plan:
    """144 variables: grid, solar, charge, discharge, energy, charge-mode per hour."""
    battery = scenario.battery
    lower = np.zeros(144)
    upper = np.full(144, np.inf)
    objective = np.zeros(144)
    integer = np.zeros(144)
    for h, row in enumerate(scenario.hours):
        i = h * 6
        objective[i] = row.tariff_bdt_per_kwh
        upper[i + 1] = row.solar_kwh
        upper[i + 2] = battery.max_charge_kwh_per_hour
        upper[i + 3] = battery.max_discharge_kwh_per_hour
        lower[i + 4], upper[i + 4] = battery.minimum_energy_kwh, battery.capacity_kwh
        upper[i + 5], integer[i + 5] = 1, 1
    for directive in directives:
        a = directive.structured_adjustment
        if a is None:
            continue
        for h in a.hours:
            i = h * 6
            match directive.directive_type:
                case "solar_reduction":
                    # Each constraint refers to the ORIGINAL forecast; satisfy all overlaps.
                    upper[i + 1] = min(upper[i + 1], scenario.hours[h].solar_kwh * a.factor)
                case "minimum_battery_reserve":
                    lower[i + 4] = max(lower[i + 4], a.minimum_energy_kwh)
                case "no_charge_window":
                    upper[i + 2] = 0
                case "no_discharge_window":
                    upper[i + 3] = 0
                case "max_grid_window":
                    upper[i] = min(upper[i], a.max_grid_kwh)

    rows, lows, highs = [], [], []

    def constraint(values, low, high):
        row = np.zeros(144)
        for idx, value in values.items():
            row[idx] = value
        rows.append(row)
        lows.append(low)
        highs.append(high)

    for h, hour in enumerate(scenario.hours):
        i = h * 6
        constraint({i: 1, i + 1: 1, i + 2: -1, i + 3: 1}, hour.demand_kwh, hour.demand_kwh)
        state = {i + 4: 1, i + 2: -1, i + 3: 1}
        rhs = battery.initial_energy_kwh if h == 0 else 0
        if h:
            state[i - 2] = -1
        constraint(state, rhs, rhs)
        constraint({i + 2: 1, i + 5: -battery.max_charge_kwh_per_hour}, -np.inf, 0)
        constraint({i + 3: 1, i + 5: battery.max_discharge_kwh_per_hour},
                   -np.inf, battery.max_discharge_kwh_per_hour)
    constraint({23 * 6 + 4: 1}, battery.initial_energy_kwh, battery.initial_energy_kwh)
    result = milp(objective, integrality=integer, bounds=Bounds(lower, upper),
                  constraints=LinearConstraint(np.array(rows), lows, highs),
                  options={"time_limit": 3.0, "mip_rel_gap": 0.0})
    if result.status == 2:
        raise InfeasibleError("No feasible schedule for the interpreted constraints")
    if not result.success:
        raise RuntimeError("Optimizer did not establish an optimal solution")

    def clean(value):
        return round(max(0.0, float(value)), 9)

    hours = []
    for h in range(24):
        grid, solar, charge, discharge, energy, _ = result.x[h * 6:h * 6 + 6]
        net = clean(charge) - clean(discharge)
        action = "charge" if net > 1e-8 else "discharge" if net < -1e-8 else "idle"
        hours.append(PlanHour(hour=h, grid_kwh=clean(grid), solar_used_kwh=clean(solar),
                              battery_action=action, battery_kwh=abs(net) if action != "idle" else 0.0,
                              battery_energy_after_kwh=clean(energy)))
    total = sum(h.grid_kwh * scenario.hours[h.hour].tariff_bdt_per_kwh for h in hours)
    return Plan(scenario_id=scenario.scenario_id, directive_interpretation=directives,
                hourly_plan=hours, total_grid_kwh=sum(h.grid_kwh for h in hours),
                total_cost_bdt=total, peak_grid_kwh=max(h.grid_kwh for h in hours),
                plan_summary=f"Minimum-cost 24-hour schedule: {total:.2f} BDT. "
                             f"Applied {sum(d.applies for d in directives)} operator directives; "
                             "maintained battery reserves and restored starting energy at day end.")

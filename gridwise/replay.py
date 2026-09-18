import math

from gridwise.models import Interpretation, Plan, Scenario, validate_directives


def verify(scenario: Scenario, plan: Plan, tolerance: float = 1e-5) -> None:
    """Independent arithmetic replay; never reuses optimizer matrices or bounds."""
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def near(a, b):
        return abs(a - b) <= tolerance

    validate_directives(scenario, Interpretation(directive_interpretation=plan.directive_interpretation))
    require(plan.scenario_id == scenario.scenario_id, "Scenario mismatch")
    require([p.hour for p in plan.hourly_plan] == list(range(24)), "Plan hours mismatch")
    battery, energy = scenario.battery, scenario.battery.initial_energy_kwh
    for row, p in zip(scenario.hours, plan.hourly_plan):
        for value in (p.grid_kwh, p.solar_used_kwh, p.battery_kwh, p.battery_energy_after_kwh):
            require(math.isfinite(value) and value >= 0, "Invalid numeric output")
        solar = row.solar_kwh
        reserve = battery.minimum_energy_kwh
        charge = p.battery_kwh if p.battery_action == "charge" else 0
        discharge = p.battery_kwh if p.battery_action == "discharge" else 0
        require(p.battery_action != "idle" or p.battery_kwh == 0, "Idle has nonzero action")
        require(charge <= battery.max_charge_kwh_per_hour + tolerance, "Charge rate exceeded")
        require(discharge <= battery.max_discharge_kwh_per_hour + tolerance, "Discharge rate exceeded")
        for d in plan.directive_interpretation:
            a = d.structured_adjustment
            if a is None or p.hour not in a.hours:
                continue
            if d.directive_type == "solar_reduction":
                solar = min(solar, row.solar_kwh * a.factor)
            elif d.directive_type == "minimum_battery_reserve":
                reserve = max(reserve, a.minimum_energy_kwh)
            elif d.directive_type == "no_charge_window":
                require(charge <= tolerance, "Charging prohibited")
            elif d.directive_type == "no_discharge_window":
                require(discharge <= tolerance, "Discharging prohibited")
            elif d.directive_type == "max_grid_window":
                require(p.grid_kwh <= a.max_grid_kwh + tolerance, "Grid cap exceeded")
        energy += charge - discharge
        require(near(energy, p.battery_energy_after_kwh), "Battery state mismatch")
        require(reserve - tolerance <= energy <= battery.capacity_kwh + tolerance, "Battery bound violated")
        require(p.solar_used_kwh <= solar + tolerance, "Solar overused")
        require(near(p.grid_kwh + p.solar_used_kwh + discharge, row.demand_kwh + charge), "Energy imbalance")
    require(near(energy, battery.initial_energy_kwh), "End-of-day neutrality violated")
    require(near(plan.total_grid_kwh, sum(p.grid_kwh for p in plan.hourly_plan)), "Grid total mismatch")
    require(near(plan.total_cost_bdt, sum(p.grid_kwh * h.tariff_bdt_per_kwh
                                       for p, h in zip(plan.hourly_plan, scenario.hours))), "Cost mismatch")
    require(near(plan.peak_grid_kwh, max(p.grid_kwh for p in plan.hourly_plan)), "Peak mismatch")

from typing import Annotated, Literal
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

Number = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
HourNumber = Annotated[int, Field(strict=True, ge=0, le=23)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Hour(StrictModel):
    hour: HourNumber
    demand_kwh: Number
    solar_kwh: Number
    tariff_bdt_per_kwh: Number


class Battery(StrictModel):
    capacity_kwh: Number
    initial_energy_kwh: Number
    minimum_energy_kwh: Number
    max_charge_kwh_per_hour: Number
    max_discharge_kwh_per_hour: Number

    @model_validator(mode="after")
    def bounds(self):
        if not self.minimum_energy_kwh <= self.initial_energy_kwh <= self.capacity_kwh:
            raise ValueError("Battery requires minimum <= initial <= capacity")
        return self


class Scenario(StrictModel):
    scenario_id: Annotated[str, Field(min_length=1)]
    operator_notes: Annotated[list[str], Field(min_length=1, max_length=3)]
    hours: Annotated[list[Hour], Field(min_length=24, max_length=24)]
    battery: Battery

    @model_validator(mode="after")
    def complete(self):
        if any(not n.strip() for n in self.operator_notes):
            raise ValueError("Operator notes must not be blank")
        if sorted(h.hour for h in self.hours) != list(range(24)):
            raise ValueError("Hours must contain each hour 0 through 23 exactly once")
        self.hours.sort(key=lambda h: h.hour)
        return self


class Window(StrictModel):
    hours: list[HourNumber]

    @model_validator(mode="after")
    def ordered(self):
        if not self.hours or self.hours != sorted(set(self.hours)):
            raise ValueError("Directive hours must be nonempty, unique and ascending")
        return self


class Solar(Window):
    factor: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]


class Reserve(Window):
    minimum_energy_kwh: Number


class GridCap(Window):
    max_grid_kwh: Number


DirectiveType = Literal["solar_reduction", "minimum_battery_reserve", "no_charge_window",
                        "no_discharge_window", "max_grid_window", "no_op"]


class Directive(StrictModel):
    note_index: Annotated[int, Field(strict=True, ge=0)]
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Solar | Reserve | GridCap | Window | None
    explanation: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def shape(self):
        shapes = {"solar_reduction": Solar, "minimum_battery_reserve": Reserve,
                  "max_grid_window": GridCap, "no_charge_window": Window,
                  "no_discharge_window": Window, "no_op": type(None)}
        if type(self.structured_adjustment) is not shapes[self.directive_type]:
            raise ValueError("Adjustment does not match directive type")
        if self.applies != (self.directive_type != "no_op"):
            raise ValueError("Only no_op may have applies=false")
        return self


class Interpretation(StrictModel):
    directive_interpretation: list[Directive]


def validate_directives(scenario: Scenario, interpretation: Interpretation) -> list[Directive]:
    directives = interpretation.directive_interpretation
    if [d.note_index for d in directives] != list(range(len(scenario.operator_notes))):
        raise ValueError("Expected one directive per note in note_index order")
    for d in directives:
        if isinstance(d.structured_adjustment, Reserve):
            if d.structured_adjustment.minimum_energy_kwh > scenario.battery.capacity_kwh:
                raise ValueError("Reserve exceeds battery capacity")
       
        if d.applies:
            ranges = list(re.finditer(
                r"\b(?:from|between)\s+(1[0-2]|0?[1-9])(?::00)?\s*(am|pm)\s+"
                r"(?:to|until|and)\s+(1[0-2]|0?[1-9])(?::00)?\s*(am|pm)\b",
                scenario.operator_notes[d.note_index], flags=re.IGNORECASE))
            if len(ranges) == 1:
                start, start_period, end, end_period = ranges[0].groups()
                start = int(start) % 12 + (12 if start_period.lower() == 'pm' else 0)
                end = int(end) % 12 + (12 if end_period.lower() == 'pm' else 0)
                if start != end:
                    expected = list(range(start, end)) if start < end else sorted(
                        list(range(start, 24)) + list(range(end)))
                    if d.structured_adjustment.hours != expected:
                        raise ValueError("Directive hours do not match the explicit AM/PM window")
    return directives


class PlanHour(StrictModel):
    hour: HourNumber
    grid_kwh: Number
    solar_used_kwh: Number
    battery_action: Literal["charge", "discharge", "idle"]
    battery_kwh: Number
    battery_energy_after_kwh: Number


class Plan(StrictModel):
    scenario_id: str
    directive_interpretation: list[Directive]
    hourly_plan: Annotated[list[PlanHour], Field(min_length=24, max_length=24)]
    total_grid_kwh: Number
    total_cost_bdt: Number
    peak_grid_kwh: Number
    plan_summary: str

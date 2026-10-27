from typing import Literal

from pydantic import Field, model_validator

from app.schemas.v2.object_query import StrictModel


class ProcessState(StrictModel):
    key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,99}$")
    label: str = Field(min_length=1, max_length=200)
    initial: bool = False
    terminal: bool = False


class ProcessTransition(StrictModel):
    key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,99}$")
    label: str = Field(min_length=1, max_length=200)
    from_state: str = Field(alias="from", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,99}$")
    to_state: str = Field(alias="to", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,99}$")
    permission_rules: list[dict] = Field(default_factory=list, max_length=20)


class ProcessDefinition(StrictModel):
    states: list[ProcessState] = Field(min_length=2, max_length=50)
    transitions: list[ProcessTransition] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def graph_contract(self):
        states, transitions = {row.key: row for row in self.states}, {row.key for row in self.transitions}
        if len(states) != len(self.states) or len(transitions) != len(self.transitions):
            raise ValueError("State and transition keys must be unique")
        if sum(row.initial for row in self.states) != 1:
            raise ValueError("Exactly one initial state is required")
        for row in self.transitions:
            if row.from_state not in states or row.to_state not in states or row.from_state == row.to_state:
                raise ValueError(f"Transition {row.key} must connect two declared, distinct states")
            if states[row.from_state].terminal:
                raise ValueError(f"Terminal state {row.from_state} cannot have outgoing transitions")
        initial = next(row.key for row in self.states if row.initial)
        reached, changed = {initial}, True
        while changed:
            changed = False
            for row in self.transitions:
                if row.from_state in reached and row.to_state not in reached:
                    reached.add(row.to_state); changed = True
        if set(states) != reached:
            raise ValueError(f"Unreachable states: {sorted(set(states) - reached)}")
        return self


class CreateProcessModel(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    entity_type_name: str = Field(min_length=1, max_length=200)
    state_property: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,99}$")
    definition: ProcessDefinition


class UpdateProcessModel(StrictModel):
    expected_etag: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    definition: ProcessDefinition | None = None


class PublishProcessModel(StrictModel):
    expected_etag: int = Field(ge=1)

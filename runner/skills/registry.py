"""Offering skills to a model, and enforcing what loading one implies (L2).

Two objects.

:class:`SkillRegistry` knows which skills exist, which the policy permits, and
which this deployment can actually run. A skill the policy does not name is not
offered — same default-deny as tools, for the same reason: a capability that
appears because a file was dropped in a directory is not a capability anyone
decided to have.

:class:`SkillfulExecutor` is the seam. It wraps the real tool executor and adds
one tool, ``skill``, whose whole job is to hand the model an instruction it
asked for. That is what makes progressive disclosure a security property rather
than a token optimisation: bodies stay out of the transcript until they are
needed, and the transcript is what crosses.

Loading a skill is a recorded event, and it can change where the rest of the run
may execute. ``pins: local`` raises the working set the instant the instruction
is handed over — before the model has done anything with it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from loguru import logger

from runner.audit.ledger import Ledger
from runner.kernel.types import ToolCall, ToolResult, ToolSpec
from runner.policy.classifier import WorkingSet
from runner.skills.models import Skill

__all__ = ["SKILL_TOOL", "SkillRegistry", "SkillfulExecutor"]

SKILL_TOOL_NAME = "skill"


class SkillRegistry:
    """The skills a run may use, filtered by policy and by what is deployed."""

    def __init__(
        self,
        skills: Mapping[str, Skill],
        *,
        allowed: Sequence[str] | None = None,
        vision: bool = False,
        allowed_tools: Sequence[str] = (),
        context_window: int = 0,
    ) -> None:
        self._skills = dict(skills)
        self._allowed = set(allowed or ())
        self._vision = vision
        self._allowed_tools = tuple(allowed_tools)
        self._context_window = context_window

    def __len__(self) -> int:
        return len(self.available())

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def permitted(self, name: str) -> bool:
        return name in self._allowed

    def available(self) -> tuple[Skill, ...]:
        """Skills that are permitted *and* runnable here, in name order.

        None when the policy does not allow the ``skill`` tool itself: the gate
        would refuse every load, so offering skills would only promise work
        that cannot happen.
        """
        if SKILL_TOOL_NAME not in self._allowed_tools:
            return ()
        usable = []
        for name in sorted(self._skills):
            if name not in self._allowed:
                continue
            skill = self._skills[name]
            ok, why = skill.satisfied_by(
                vision=self._vision,
                allowed_tools=self._allowed_tools,
                context_window=self._context_window,
            )
            if ok:
                usable.append(skill)
            else:
                logger.debug(f"skill {name} is permitted but not usable here: {why}")
        return tuple(usable)

    def unusable(self) -> tuple[tuple[str, str], ...]:
        """Permitted skills this deployment cannot run, and why — for `status`."""
        if SKILL_TOOL_NAME not in self._allowed_tools:
            reason = "the policy does not allow the `skill` tool (tools.allow.skill)"
            return tuple((name, reason) for name in sorted(self._skills) if name in self._allowed)
        blocked = []
        for name in sorted(self._skills):
            if name not in self._allowed:
                continue
            ok, why = self._skills[name].satisfied_by(
                vision=self._vision,
                allowed_tools=self._allowed_tools,
                context_window=self._context_window,
            )
            if not ok:
                blocked.append((name, why))
        return tuple(blocked)

    def catalogue(self) -> str:
        """One line per available skill: what the model sees before choosing."""
        return "\n".join(f"- {skill.summary()}" for skill in self.available())

    def spec(self) -> ToolSpec:
        """The ``skill`` tool, with the catalogue in its description.

        The catalogue lives in the tool description rather than in the system
        prompt so that it travels with the tool definition, and so a model that
        never calls the tool never carries the list into a later turn.
        """
        names = [skill.name for skill in self.available()]
        catalogue = self.catalogue() or "- (none available)"
        return ToolSpec(
            name=SKILL_TOOL_NAME,
            description=(
                "Load the instructions for one skill before attempting a task it "
                "covers. Call this first, follow what it returns, then act.\n\n"
                f"Available skills:\n{catalogue}"
            ),
            schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill to load.",
                        **({"enum": names} if names else {}),
                    }
                },
                "required": ["name"],
            },
        )


SKILL_TOOL = SKILL_TOOL_NAME


class SkillfulExecutor:
    """Wraps an executor and adds the ``skill`` tool. Satisfies ``ToolExecutor``."""

    def __init__(
        self,
        inner: object,
        registry: SkillRegistry,
        working_set: WorkingSet,
        ledger: Ledger | None = None,
    ) -> None:
        self._inner = inner
        self._registry = registry
        self._working_set = working_set
        self._ledger = ledger
        self.loaded: list[str] = []

    def specs(self) -> tuple[ToolSpec, ...]:
        inner_specs: tuple[ToolSpec, ...] = self._inner.specs()  # type: ignore[attr-defined]
        if not self._registry.available():
            return inner_specs
        return (*inner_specs, self._registry.spec())

    def invoke(self, call: ToolCall) -> ToolResult:
        if call.name != SKILL_TOOL_NAME:
            return self._inner.invoke(call)  # type: ignore[attr-defined]

        name = str(call.arguments.get("name", "")).strip()
        skill = self._registry.get(name)

        if skill is None or not self._registry.permitted(name):
            # A skill that exists on disk but is not permitted is reported the
            # same way as one that does not exist: the model learns nothing
            # about what the operator chose not to enable.
            return self._refuse(call, f"no skill named {name!r} is available")

        usable = {s.name for s in self._registry.available()}
        if name not in usable:
            reason = dict(self._registry.unusable()).get(name, "not usable in this deployment")
            return self._refuse(call, f"skill {name!r} cannot run here: {reason}")

        if skill.pins_local:
            # Before the instruction is handed over, not after: from here the run
            # is confined, whatever it goes on to do.
            self._working_set.observe(f"skill:{name}", skill.floor)

        self.loaded.append(name)
        self._record(skill)

        return ToolResult(call_id=call.id, name=call.name, content=skill.body)

    def _refuse(self, call: ToolCall, reason: str) -> ToolResult:
        if self._ledger is not None:
            self._ledger.record(
                "skill",
                outcome="held",
                klass=self._working_set.klass,
                substrate="local",
                detail={"skill": str(call.arguments.get("name", "")), "reason": reason},
            )
        return ToolResult(call_id=call.id, name=call.name, content={"error": reason}, is_error=True)

    def _record(self, skill: Skill) -> None:
        if self._ledger is None:
            return
        self._ledger.record(
            "skill",
            outcome="cleared",
            klass=self._working_set.klass,
            substrate="local",
            detail={
                "skill": skill.name,
                "version": skill.version,
                "pins": skill.pins,
                "reason": (
                    "loaded; the run is pinned to the perimeter" if skill.pins_local else "loaded"
                ),
            },
        )

"""
Base Tool Class

A tool declares what it accepts as a typed arguments model, and that model is
the only description of its arguments there is: the schema advertised to the
model is derived from it, and every generic caller validates against it.

The base used to declare ``execute(self, **kwargs) -> Any`` while every tool
narrowed it to its own required arguments — a Liskov violation the type checker
reported six times, benign only because no caller dispatched over ``Tool``
generically (#8). Two callers do (the agent loop's executor and the task
executor), and grammar-constrained tool calls (#1) will be a third: all of them
ask a tool what it accepts without knowing which tool they hold. So the base
no longer claims an ``execute`` it cannot honour. It offers :meth:`Tool.run`,
which takes the arguments as data, and each tool keeps its own ``execute`` with
its own honest signature.

Fields that name material on disk are typed :data:`FilePath` rather than
``str``. Nothing reads that yet; it exists so the perimeter can one day take a
path from where the tool declares it instead of pattern-matching it out of
strings, and so the model does not foreclose that.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, Generic, TypeVar, get_args, get_origin

from pydantic import BaseModel, ConfigDict


class Material:
    """Marks an argument whose value names material: a file or a directory."""

    def __repr__(self) -> str:
        return "Material()"


FilePath = Annotated[str, Material()]
"""A path argument. Validates as ``str``; declares that the value is material."""


class ToolArguments(BaseModel):
    """What one tool accepts. Subclass per tool, one field per argument.

    Extra keys are allowed and forwarded, as ``**kwargs`` always forwarded them:
    tools read a few undocumented options (``ocr``, ``within``) that are not
    offered to the model, and tightening that is a separate change.
    """

    model_config = ConfigDict(extra="allow")


ArgsT = TypeVar("ArgsT", bound=ToolArguments)


class Tool(ABC, Generic[ArgsT]):
    """Base class per un tool"""

    arguments: ClassVar[type[ToolArguments]]
    """The tool's arguments model. Set on every concrete tool."""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.parameters = parameters_schema(self.arguments)

    def run(self, arguments: Mapping[str, Any]) -> Any:
        """Validate ``arguments`` against this tool's model, then run it.

        The entry point for anything that holds a ``Tool`` without knowing which.
        Malformed arguments raise :class:`pydantic.ValidationError`, which says
        which field was wrong — the executors turn it into a tool error the model
        can retry from, as they did the ``TypeError`` it replaces.
        """
        return self.call(self.arguments.model_validate(dict(arguments)))  # type: ignore[arg-type]

    @abstractmethod
    def call(self, args: ArgsT) -> Any:
        """Run the tool with validated arguments. Returns a JSON-serialisable value."""

    @classmethod
    def material_fields(cls) -> tuple[str, ...]:
        """The arguments of this tool that name material on disk."""
        return tuple(
            name
            for name, field in cls.arguments.model_fields.items()
            if any(isinstance(meta, Material) for meta in field.metadata)
            or _names_material(field.annotation)
        )


def _names_material(annotation: Any) -> bool:
    """Whether a type carries the :class:`Material` marker anywhere inside it.

    Pydantic lifts the marker into ``field.metadata`` only when it annotates the
    field itself; ``FilePath | None`` keeps it inside the union.
    """
    if get_origin(annotation) is Annotated:
        _, *metadata = get_args(annotation)
        if any(isinstance(meta, Material) for meta in metadata):
            return True
    return any(_names_material(arg) for arg in get_args(annotation))


def forwarded(args: ToolArguments) -> dict[str, Any]:
    """The arguments a caller actually passed, for a tool's ``execute``.

    Only what was set — so the defaults in ``execute`` apply, as they did when
    the arguments arrived as ``**kwargs``, and do not have to be repeated in the
    model — plus any extra keys.
    """
    return args.model_dump(exclude_unset=True)


def parameters_schema(model: type[ToolArguments]) -> dict[str, Any]:
    """The JSON schema advertised to the model, derived from an arguments model.

    Pydantic's schema, trimmed to what a tool schema has always carried: no
    titles, no defaults (a default is described in words, where the model reads
    it), and an optional argument written as its type rather than as "that type
    or null".
    """
    raw = model.model_json_schema()
    properties = {name: _plain(spec) for name, spec in raw.get("properties", {}).items()}
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    required = [name for name, field in model.model_fields.items() if field.is_required()]
    if required:
        schema["required"] = required
    return schema


def _plain(spec: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in spec.items() if k not in ("title", "default")}
    if out.get("additionalProperties") is True:  # JSON Schema's default, said aloud
        del out["additionalProperties"]
    options = out.pop("anyOf", None)
    if options is not None:
        concrete = [o for o in options if o.get("type") != "null"]
        if len(concrete) == 1:
            out = {**_plain(concrete[0]), **out}
        else:
            out["anyOf"] = [_plain(o) for o in options]
    if isinstance(out.get("items"), dict):
        out["items"] = _plain(out["items"])
    return out

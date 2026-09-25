"""Every tool declares what it accepts, and generic callers go through that (#8).

The base used to promise ``execute(**kwargs)`` while every tool required its own
arguments. These tests pin the replacement: a typed arguments model per tool,
one generic entry point that validates against it, and a schema for the model
derived from the same declaration rather than written out a second time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import pytest
from pydantic import Field, ValidationError

from runner.tools.base import FilePath, Tool, ToolArguments, forwarded, parameters_schema
from runner.tools.registry import ToolRegistry

ALL = ["filesystem", "shell", "browser", "document_reader", "explorer", "memory_search"]


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry({"tools": {"enabled": ALL}})


class _Echo(ToolArguments):
    text: Annotated[str, Field(description="What to echo")]
    times: Annotated[int | None, Field(description="How often (default: 1)")] = None
    where: Annotated[FilePath | None, Field(description="A file")] = None


class _EchoTool(Tool[_Echo]):
    arguments = _Echo

    def __init__(self) -> None:
        super().__init__(name="echo", description="Echo")

    def call(self, args: _Echo) -> Any:
        return self.execute(**forwarded(args))

    def execute(self, text: str, times: int = 1, **kwargs: Any) -> dict[str, Any]:
        return {"text": text * times, "extra": kwargs}


def test_the_base_no_longer_claims_an_execute_it_cannot_honour():
    assert not hasattr(Tool, "execute")


def test_every_registered_tool_declares_its_arguments(registry):
    for name in registry.list_tools():
        tool = registry.get_tool(name)
        assert issubclass(tool.arguments, ToolArguments), name


def test_the_advertised_schema_is_derived_from_the_arguments(registry):
    """One description of a tool's arguments, not two kept in step by hand."""
    for name in registry.list_tools():
        tool = registry.get_tool(name)
        assert tool.parameters == parameters_schema(tool.arguments), name


def test_the_derived_schema_is_the_one_models_were_already_shown(registry):
    """Deriving the schema changed nothing a model sees: no titles, no defaults,
    no "or null" — the document_reader schema, byte for byte as it was written."""
    assert registry.get_tool("document_reader").parameters == {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or ~ path to the file to read"},
            "max_chars": {
                "type": "integer",
                "description": "Max characters to return (default: 100000). Use 0 for no limit.",
            },
            "sheet_name": {
                "type": "string",
                "description": "For spreadsheets: the sheet to read (default: every sheet)",
            },
        },
        "required": ["path"],
    }


def test_a_call_missing_a_required_argument_names_it():
    """Malformed arguments are a tool error the model can retry from — and now
    one that says which field, rather than a TypeError about a signature."""
    with pytest.raises(ValidationError, match="text"):
        _EchoTool().run({})


def test_a_value_outside_an_enum_is_refused_before_the_tool_runs(registry, tmp_path):
    with pytest.raises(ValidationError, match="operation"):
        registry.get_tool("filesystem").run({"operation": "format-disk", "path": str(tmp_path)})


def test_only_what_was_passed_reaches_execute_so_its_defaults_apply():
    assert _EchoTool().run({"text": "ab"})["text"] == "ab"
    assert _EchoTool().run({"text": "ab", "times": 2})["text"] == "abab"


def test_keys_that_are_not_advertised_still_reach_the_tool():
    """`within` on memory_search and `ocr` on document_reader are set by the
    perimeter and by tests, never offered to the model; they must still arrive."""
    assert _EchoTool().run({"text": "x", "within": ["/a"]})["extra"] == {"within": ["/a"]}


def test_path_arguments_are_declared_as_material(registry):
    assert _EchoTool.material_fields() == ("where",)
    assert registry.get_tool("document_reader").material_fields() == ("path",)
    assert registry.get_tool("filesystem").material_fields() == ("path",)
    assert registry.get_tool("explorer").material_fields() == ("path",)
    assert registry.get_tool("shell").material_fields() == ("cwd",)
    assert registry.get_tool("browser").material_fields() == ()


def test_a_real_tool_runs_through_the_generic_entry_point(registry, tmp_path: Path):
    target = tmp_path / "note.txt"
    target.write_text("hello", encoding="utf-8")

    result = registry.get_tool("document_reader").run({"path": str(target)})

    assert result["success"] is True
    assert "hello" in result["content"]

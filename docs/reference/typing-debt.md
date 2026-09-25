# Typing debt

`mypy` runs over the whole package and passes. It passes because three modules are
listed in a ledger in `pyproject.toml` under `ignore_errors`, not because they are
clean.

They are listed **individually rather than by wildcard** so the debt is countable
and shrinks by deletion: type a module, delete its line, and the gate tightens
permanently. A blanket exclusion would have hidden the same errors and never
shrunk.

New code has no exemption. `runner/kernel`, `runner/capability` and `runner/agent`
are checked with `disallow_untyped_defs`, `warn_return_any` and
`no_implicit_optional`.

## The ledger

| Module | Errors | Nature |
|---|---|---|
| `runner.cli` | 2 | `str \| None` passed where `str` is required |
| `runner.local_api` | 2 | **Real defect** — see below |
| `runner.tui` | — | Untyped module, notes only |

## One of these is a defect, not an annotation gap

It is left in place deliberately. Phase 0 is a refactor that changes no
behaviour and the fix changes behaviour — it belongs in its own change, with its
own tests.

### `runner/local_api.py:225,257` — a missing note raises instead of 404

```python
NoteOut.from_note(note)   # note is Note | None
```

`get_note` can return `None`. When it does, `from_note` receives `None` and the
endpoint raises rather than returning a 404. A request for a note that does not
exist produces a 500.

The fix is a `None` check and an `HTTPException(404)`, which changes the
response an existing client sees — hence not here.

## Paid

### `runner/tools/*` — one defect, six reports (#8)

`Tool.execute` was declared as `execute(self, **kwargs) -> Any` while every
concrete tool required its own arguments: a Liskov violation, reported once per
tool. It stopped being benign the moment something dispatched over `Tool`
generically — the agent loop's executor already did, and grammar-constrained
tool calls (#1) will.

The base no longer declares `execute`. Each tool declares a typed arguments
model (`Tool[Args]`, `arguments = Args`), the schema advertised to the model is
derived from it (and is byte-identical to the one written out by hand before),
and generic callers use `Tool.run(arguments)`, which validates against the
model and then calls the tool's own `execute`. Path arguments are typed
`FilePath`, so `Tool.material_fields()` can say which arguments name material —
the hook the classifier needs to stop pattern-matching paths out of tool
arguments. `runner.tools.*` is off the ledger, extractors included.

## Fixing one

```bash
# 1. Remove the module from the ledger in pyproject.toml
# 2. See what it costs
env/bin/mypy
# 3. Fix, then make sure nothing moved
make check
```

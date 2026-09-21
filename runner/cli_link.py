"""``annona link``: offer this machine to Agents Studio as an executor.

Four commands, because the link has four states a person needs to act on:
not enrolled (``enroll``), enrolled and idle (``status``), serving (``serve``),
and done with it (``forget``). The design is ADR 0006.
"""

from __future__ import annotations

import signal
import threading

import typer
from rich.console import Console

from runner import __version__
from runner.kernel.types import SensitivityClass
from runner.link import (
    LinkClient,
    LinkConfig,
    LinkError,
    LinkRevokedError,
    LinkWorker,
    enroll,
    link_path,
)
from runner.policy.loader import load_policy
from runner.service_urls import resolve_service_url
from runner.services.enforcement import policy_path

console = Console()

link_app = typer.Typer(
    name="link",
    help="Offer this machine to Agents Studio as an executor (outbound only).",
    no_args_is_help=True,
)

_LINK_SECTION = """
# ── Link to Agents Studio (ADR 0006) ──────────────────────────────────────────
# The highest class whose ANSWER may go back to Studio. A run that read anything
# above it — or touched sealed material, or produced an answer that itself
# classifies above it — is reported as `withheld`: Studio learns it finished and
# where it ran, and the answer stays on this machine. Lower it to `public` to
# send back only answers built from public material; remove the section to send
# back metadata only.
link:
  release: {release}
"""


def _ensure_link_section(release: str) -> str:
    """Add ``link.release`` to the policy if it has none. Returns what is in force."""
    path = policy_path()
    policy = load_policy(path)  # refuses to enroll a runner with no valid perimeter
    if policy.link.release is not None:
        return policy.link.release.label
    # Validated before anything is written: a bad --release must not leave the
    # perimeter's own file unparseable.
    label = SensitivityClass.parse(release).label
    original = path.read_text(encoding="utf-8")
    # Appended as text, not re-dumped, so the operator's comments survive.
    path.write_text(
        original.rstrip() + "\n" + _LINK_SECTION.format(release=label), encoding="utf-8"
    )
    written = load_policy(path).link.release
    if written is None:  # e.g. an existing `link:` without `release` shadowed by YAML
        path.write_text(original, encoding="utf-8")
        raise LinkError("could not add link.release to the policy; set it by hand")
    return written.label


@link_app.command("enroll")
def enroll_cmd(
    code: str = typer.Argument(..., help="Single-use code from Studio → Runner → Aggiungi"),
    name: str = typer.Option(
        ..., "--name", "-n", help="How this machine appears in Studio, e.g. dgx1"
    ),
    endpoint: str = typer.Option(
        "", "--endpoint", help="Studio's AI backend (https only); default: the production one"
    ),
    release: str = typer.Option(
        "internal", "--release", help="link.release to write if the policy has none"
    ),
):
    """Trade an enrollment code for this runner's own credential."""
    try:
        in_force = _ensure_link_section(release)
        config = enroll(endpoint or resolve_service_url("ai"), code, name, version=__version__)
        where = config.save()
    except (LinkError, Exception) as exc:  # noqa: BLE001 — every failure is a sentence
        console.print(f"❌ [red]{exc}[/red]")
        raise typer.Exit(1) from None
    console.print(
        f"✅ [green]{name}[/green] enrolled in [bold]{config.organization or 'your organisation'}[/bold]"
    )
    console.print(f"   credential  {where} (0600)")
    console.print(
        f"   link.release  [bold]{in_force}[/bold] — answers above it stay on this machine"
    )
    console.print("   next: [cyan]annona link serve[/cyan]  (outbound HTTPS only; nothing listens)")


@link_app.command("status")
def status_cmd():
    """Show whether this machine is enrolled, and what it would send back."""
    try:
        config = LinkConfig.load()
    except LinkError as exc:
        console.print(f"❌ [red]{exc}[/red]")
        raise typer.Exit(1) from None
    if config is None:
        console.print(
            "not enrolled — get a code in Studio, then [cyan]annona link enroll <code> --name …[/cyan]"
        )
        return
    try:
        release = load_policy(policy_path()).link.release
    except Exception as exc:  # noqa: BLE001
        console.print(f"enrolled as {config.name}, but the policy does not load: {exc}")
        raise typer.Exit(1) from None
    console.print(
        f"enrolled as [bold]{config.name}[/bold] ({config.runner_id}) → {config.endpoint}"
    )
    console.print(
        f"link.release: [bold]{release.label if release else 'none — metadata only'}[/bold]"
    )


@link_app.command("serve")
def serve_cmd():
    """Poll Studio for work and run it through the perimeter. Ctrl-C to stop."""
    try:
        config = LinkConfig.load()
        if config is None:
            raise LinkError("not enrolled: run `annona link enroll <code> --name …` first")
        load_policy(policy_path())  # no perimeter, no remote work
    except Exception as exc:  # noqa: BLE001
        console.print(f"❌ [red]{exc}[/red]")
        raise typer.Exit(1) from None

    # Imported here: building the executor loads providers, and the other link
    # commands should not pay for it.
    from runner.config import ConfigManager  # noqa: PLC0415
    from runner.executor import TaskExecutor  # noqa: PLC0415

    manager = ConfigManager()
    if not manager.config_exists():
        manager.create_default_config()
    executor = TaskExecutor(manager.load_config())

    def run(instruction, cancelled):
        return executor.ai_client.reason_and_execute(
            prompt=instruction,
            context={"source": "link", "surface": "agents-studio"},
            tools=executor.tools,
            permissions=executor.permissions,
            cancel=cancelled,
        )

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    console.print(
        f"🔗 [bold]{config.name}[/bold] serving {config.endpoint} — outbound only, Ctrl-C to stop"
    )
    try:
        LinkWorker(LinkClient(config), run, version=__version__).serve(stop)
    except LinkRevokedError as exc:
        console.print(f"⛔ [red]{exc}[/red]")
        raise typer.Exit(3) from None


@link_app.command("forget")
def forget_cmd():
    """Delete this machine's credential. Revoke it in Studio as well."""
    path = link_path()
    if path.exists():
        path.unlink()
        console.print(
            f"🗑  removed {path}. Revoke the runner in Studio too, so the old secret is dead there."
        )
    else:
        console.print("nothing to forget: this machine is not enrolled")


def register(app: typer.Typer) -> None:
    app.add_typer(link_app)

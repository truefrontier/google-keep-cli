"""Unofficial read-only CLI for Google Keep. Session stays in the local Chrome profile."""

import asyncio
import json
import re
from pathlib import Path

import click
from playwright.async_api import async_playwright
from rich.console import Console
from rich.table import Table

from keep_cli.agent_ux import (
    EXIT_NOT_FOUND,
    agent_output_options,
    die,
    emit_csv,
    emit_json,
    note_showing,
    resolve_format,
    select_fields,
)


PROFILE = Path.home() / ".keep-cli" / "chrome-profile"
STEALTH = ["--disable-blink-features=AutomationControlled"]
console = Console()


def has_session() -> bool:
    return PROFILE.exists() and any(PROFILE.iterdir())


def need_session() -> None:
    if not has_session():
        from keep_cli.agent_ux import EXIT_AUTH, die
        die("No session found.", EXIT_AUTH, hint="Run: keep login")


async def open_context(playwright, headless: bool):
    return await playwright.chromium.launch_persistent_context(
        str(PROFILE),
        headless=headless,
        channel="chrome",
        args=STEALTH,
    )


async def login() -> None:
    PROFILE.parent.mkdir(parents=True, exist_ok=True)
    console.print("Opening Chrome. Sign in to Google Keep. This window closes once Keep loads.")
    async with async_playwright() as p:
        context = await open_context(p, headless=False)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://keep.google.com/", wait_until="domcontentloaded")
        for _ in range(180):
            await asyncio.sleep(5)
            urls = []
            for pg in context.pages:
                try:
                    urls.append(pg.url)
                except Exception:
                    continue
            if any(
                "keep.google.com" in u
                and "accounts.google." not in u
                and "signin" not in u.lower()
                for u in urls
            ):
                await context.close()
                console.print("[green]Session saved.[/green]")
                return
        await context.close()
        raise RuntimeError("Login timed out. Run: keep login")


def normalize_node(node: dict) -> dict | None:
    if not isinstance(node, dict):
        return None
    if node.get("kind") != "notes#node":
        return None
    ntype = node.get("type") or ""
    if ntype not in {"NOTE", "LIST"}:
        return None
    parent = node.get("parentId")
    if parent not in (None, "", "root"):
        return None
    title = (node.get("title") or "").strip()
    text = (node.get("indexableText") or "").strip()
    preview = ""
    pdata = node.get("previewData") or {}
    if isinstance(pdata, dict) and not text:
        # fall back: no indexableText
        pass
    timestamps = node.get("timestamps") or {}
    return {
        "id": node.get("id") or "",
        "server_id": node.get("serverId") or "",
        "type": ntype,
        "title": title,
        "text": text,
        "pinned": bool(node.get("isPinned")),
        "archived": bool(node.get("isArchived")),
        "color": node.get("color") or "",
        "updated": (timestamps.get("userEdited") or timestamps.get("updated") or ""),
        "created": timestamps.get("created") or "",
        "labels": [
            (x.get("labelId") if isinstance(x, dict) else str(x))
            for x in (node.get("labelIds") or [])
        ],
    }


async def sync_notes(include_archived: bool = False, limit: int | None = None) -> list[dict]:
    notes: dict[str, dict] = {}
    done = asyncio.get_running_loop().create_future()

    async with async_playwright() as p:
        context = await open_context(p, headless=True)
        page = context.pages[0] if context.pages else await context.new_page()

        async def on_response(response):
            if "notes/v1/changes" not in response.url or response.request.method != "POST":
                return
            try:
                data = await response.json()
            except Exception:
                return
            if not isinstance(data, dict):
                return
            for node in data.get("nodes") or []:
                item = normalize_node(node)
                if not item or not item["id"]:
                    continue
                if item["archived"] and not include_archived:
                    continue
                notes[item["id"]] = item
            truncated = data.get("truncated")
            # When Keep finishes a full page sync burst, truncated becomes false.
            if truncated is False and not done.done():
                done.set_result(True)

        page.on("response", on_response)
        await page.goto("https://keep.google.com/u/0/", wait_until="domcontentloaded")
        try:
            await asyncio.wait_for(done, timeout=25)
        except TimeoutError:
            # Fall through with whatever we collected while the page loaded.
            pass
        # Extra settle for trailing sync pages.
        await page.wait_for_timeout(3000)
        await context.close()

    items = list(notes.values())
    items.sort(key=lambda x: x.get("updated") or "", reverse=True)
    if limit:
        items = items[:limit]
    return items



def display_title(note: dict, width: int = 60) -> str:
    title = (note.get("title") or "").strip()
    if title:
        return title
    text = (note.get("text") or "").strip().splitlines()
    first = text[0] if text else "(untitled)"
    if len(first) > width:
        return first[: width - 1] + "…"
    return first


def note_matches(note: dict, query: str) -> bool:
    q = query.lower()
    blob = " ".join(
        [
            note.get("title") or "",
            note.get("text") or "",
            note.get("id") or "",
        ]
    ).lower()
    return q in blob


@click.group()
def main():
    """Unofficial read-only CLI for your Google Keep notes."""


@main.command("login")
def login_cmd():
    """Open Chrome so you can sign in. Session stays on this machine."""
    asyncio.run(login())


KEEP_COMPACT = ("id", "type", "title", "updated", "pinned", "archived")


@main.command("list")
@agent_output_options()
@click.option("--limit", type=int, default=None)
@click.option("--archived", is_flag=True, help="Include archived notes")
@click.option("--id-only", is_flag=True)
def list_cmd(fmt, compact, select, quiet, as_csv, limit, archived, id_only):
    """List notes from a Keep sync."""
    need_session()
    notes = asyncio.run(sync_notes(include_archived=archived, limit=limit))
    if id_only:
        for note in notes:
            click.echo(note["id"])
        return
    rows = notes
    if compact:
        rows = [
            {
                "id": n["id"],
                "type": n["type"],
                "title": display_title(n),
                "updated": n.get("updated") or "",
                "pinned": n.get("pinned"),
                "archived": n.get("archived"),
            }
            for n in notes
        ]
    rows = select_fields(rows, select)
    fmt = resolve_format(fmt)
    if as_csv or fmt == "csv":
        fields = list(rows[0].keys()) if rows else list(KEEP_COMPACT)
        emit_csv(rows if isinstance(rows, list) else [rows], fields)
        return
    if fmt == "json":
        emit_json(rows)
        return
    table = Table(title="Keep notes")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Type", style="green")
    table.add_column("Title", style="white")
    table.add_column("Updated", style="magenta")
    for note in notes:
        title = display_title(note)
        pin = "📌 " if note["pinned"] else ""
        table.add_row(note["id"], note["type"], pin + title, (note["updated"] or "")[:19])
    console.print(table)
    note_showing(len(notes), quiet=quiet, noun="notes")


@main.command()
@click.argument("query")
@agent_output_options()
@click.option("--limit", type=int, default=50)
@click.option("--archived", is_flag=True)
@click.option("--id-only", is_flag=True)
def search(query, fmt, compact, select, quiet, as_csv, limit, archived, id_only):
    """Search note titles and text."""
    need_session()
    notes = [n for n in asyncio.run(sync_notes(include_archived=archived)) if note_matches(n, query)]
    notes = notes[:limit]
    if id_only:
        for note in notes:
            click.echo(note["id"])
        return
    rows = notes
    if compact:
        rows = [
            {
                "id": n["id"],
                "title": display_title(n),
                "updated": n.get("updated") or "",
            }
            for n in notes
        ]
    rows = select_fields(rows, select)
    fmt = resolve_format(fmt)
    if as_csv:
        fields = list(rows[0].keys()) if rows else ["id", "title"]
        emit_csv(rows if isinstance(rows, list) else [rows], fields)
        return
    if fmt == "json":
        emit_json(rows)
        return
    table = Table(title=f"Search: {query}")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title", style="white")
    table.add_column("Snippet", style="green")
    for note in notes:
        title = display_title(note)
        snippet = (note["text"] or "").replace("\n", " ")[:80]
        table.add_row(note["id"], title, snippet)
    console.print(table)
    note_showing(len(notes), quiet=quiet, noun="notes")


@main.command()
@click.argument("note_id")
@agent_output_options(formats=("table", "json", "text"))
def info(note_id, fmt, compact, select, quiet, as_csv):
    """Show one note by id."""
    need_session()
    notes = asyncio.run(sync_notes(include_archived=True))
    match = None
    for note in notes:
        if note["id"] == note_id or note["server_id"] == note_id or note_id in note["id"]:
            match = note
            break
    if not match:
        die(f"No note {note_id}", EXIT_NOT_FOUND, hint="Run: keep list --format json --select id,title")
    payload = match
    if compact:
        payload = {k: match.get(k) for k in ("id", "type", "title", "updated", "pinned", "archived")}
        payload["title"] = display_title(match)
    payload = select_fields(payload, select)
    fmt = resolve_format(fmt)
    if as_csv:
        emit_csv([payload] if isinstance(payload, dict) else payload)
        return
    if fmt == "json":
        emit_json(payload)
        return
    if fmt == "text":
        if match["title"]:
            click.echo(match["title"])
            click.echo("")
        click.echo(match["text"] or "")
        return
    table = Table(title=match["title"] or match["id"])
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    for key in ("id", "type", "title", "pinned", "archived", "updated", "created"):
        table.add_row(key, str(match.get(key) or ""))
    table.add_row("text", match.get("text") or "")
    console.print(table)


@main.command("get")
@click.argument("note_id")
@click.option("-o", "out_path", type=click.Path(), default=None)
@click.option("--quiet", "-q", is_flag=True)
def get_cmd(note_id, out_path, quiet):
    """Print note text (title + body)."""
    need_session()
    notes = asyncio.run(sync_notes(include_archived=True))
    match = None
    for note in notes:
        if note["id"] == note_id or note["server_id"] == note_id or note_id in note["id"]:
            match = note
            break
    if not match:
        die(f"No note {note_id}", EXIT_NOT_FOUND, hint="Run: keep list --id-only")
    parts = []
    if match["title"]:
        parts.append(match["title"])
        parts.append("")
    parts.append(match["text"] or "")
    text_out = "\n".join(parts).rstrip() + "\n"
    if out_path:
        Path(out_path).write_text(text_out)
        if not quiet:
            click.echo(f"Wrote {out_path}", err=True)
        return
    click.echo(text_out, nl=False)


if __name__ == "__main__":
    main()

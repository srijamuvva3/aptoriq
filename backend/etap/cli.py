"""Command line entry point for the ingestion pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from .ingest import IngestOptions, load_paper, parse_paper
from .ingest.schema import ExtractionRoute, ParsedPaper
from .profiles import BUILTIN_PROFILES

app = typer.Typer(add_completion=False, help="AptoriQ ingestion and exam-runtime tools.")
console = Console()


@app.command()
def parse(
    pdf: Path = typer.Argument(..., help="Question paper PDF."),
    out: Path = typer.Option(Path("data/output"), "--out", "-o", help="Output directory."),
    key: Optional[Path] = typer.Option(None, "--key", "-k", help="Answer key (CSV or PDF)."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Exam profile id."),
    dpi: int = typer.Option(200, help="Rasterisation DPI for page renders and crops."),
    provider: Optional[str] = typer.Option(None, help="openai, anthropic or gemini."),
    model: Optional[str] = typer.Option(None, help="Override the provider's default model."),
    vision: bool = typer.Option(True, "--vision/--no-vision", help="Use a vision model on scanned pages."),
    tag: bool = typer.Option(True, "--tag/--no-tag", help="AI-tag subject, chapter and difficulty."),
    title: Optional[str] = typer.Option(None, help="Paper title; defaults to the file name."),
) -> None:
    """Parse a question paper into structured JSON plus page renders and crops."""
    load_dotenv()

    destination = out / pdf.stem
    paper = parse_paper(
        pdf,
        destination,
        key_path=key,
        options=IngestOptions(
            dpi=dpi,
            profile_id=profile,
            provider_name=provider,
            model=model,
            use_vision=vision,
            tag=tag,
            title=title,
        ),
    )
    _report(paper, destination)


@app.command()
def show(paper_json: Path = typer.Argument(..., help="A paper.json produced by parse.")) -> None:
    """Re-print the summary for an already parsed paper."""
    paper = load_paper(paper_json)
    _report(paper, paper_json.parent)


@app.command("import")
def import_paper(
    paper_json: Path = typer.Argument(..., help="A paper.json produced by parse."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Exam profile id."),
    title: Optional[str] = typer.Option(None, help="Override the paper title."),
) -> None:
    """Load a parsed paper into the database as a draft."""
    from .db import create_all, get_session_factory
    from .importer import import_parsed_file

    create_all()
    session = get_session_factory()()
    try:
        paper = import_parsed_file(session, paper_json, profile_id=profile, title=title)
        session.commit()
        console.print(
            f"Imported [bold]{paper.title}[/bold] as paper {paper.id} "
            f"with {len(paper.questions)} questions (status: draft)."
        )
    finally:
        session.close()


@app.command()
def seed(
    paper_json: Optional[Path] = typer.Option(None, help="Parsed paper to publish."),
    profile: str = typer.Option("jee_main", help="Exam profile for the seeded paper."),
) -> None:
    """Create a demo classroom with a teacher, students and one published paper."""
    from .seed import seed as run_seed

    result = run_seed(paper_json, profile_id=profile)

    table = Table(title="Demo classroom ready", show_header=False)
    table.add_column("field", style="bold")
    table.add_column("value")
    table.add_row("Paper", f"{result['paper_title']} (id {result['paper_id']})")
    table.add_row("Questions", str(result["questions"]))
    table.add_row("Assignment", str(result["assignment_id"]))
    table.add_row("Teacher login", result["teacher"])
    table.add_row("Student logins", ", ".join(result["students"]))
    table.add_row("Password", result["password"])
    console.print(table)


@app.command()
def attempts() -> None:
    """List attempts recorded so far."""
    from sqlalchemy import func, select

    from .db import create_all, get_session_factory
    from .models import Attempt, Event, User

    create_all()
    session = get_session_factory()()
    try:
        table = Table(title="Attempts")
        for column in ("id", "student", "status", "score", "events", "disconnects"):
            table.add_column(column)

        rows = session.execute(
            select(Attempt, User.username).join(User, User.id == Attempt.student_id)
        ).all()
        for attempt, username in rows:
            event_count = session.scalar(
                select(func.count()).select_from(Event).where(Event.attempt_id == attempt.id)
            )
            table.add_row(
                str(attempt.id),
                username,
                attempt.status.value,
                f"{attempt.score}/{attempt.max_score}" if attempt.score is not None else "-",
                str(event_count or 0),
                str(attempt.disconnect_count),
            )
        console.print(table)
        if not rows:
            console.print("No attempts yet.")
    finally:
        session.close()


@app.command()
def events(
    attempt_id: int = typer.Argument(..., help="Attempt to inspect."),
    limit: int = typer.Option(60, help="How many events to show."),
) -> None:
    """Print an attempt's behavioural event log.

    This is the raw material the temperament analysis will be built from, so being able to
    eyeball it is the quickest way to confirm the instrumentation is recording what it
    should.
    """
    from sqlalchemy import select

    from .db import create_all, get_session_factory
    from .models import Event, Question

    create_all()
    session = get_session_factory()()
    try:
        rows = list(
            session.scalars(
                select(Event)
                .where(Event.attempt_id == attempt_id)
                .order_by(Event.seq)
                .limit(limit)
            )
        )
        if not rows:
            console.print(f"No events recorded for attempt {attempt_id}.")
            return

        numbers = {
            question.id: question.number
            for question in session.scalars(select(Question))
        }
        first_ts = next((row.client_ts for row in rows if row.client_ts), 0)

        table = Table(title=f"Event log for attempt {attempt_id}")
        for column in ("seq", "at", "type", "question", "payload"):
            table.add_column(column)

        for row in rows:
            offset = (row.client_ts - first_ts) / 1000 if row.client_ts and first_ts else 0
            payload = ", ".join(f"{key}={value}" for key, value in (row.payload or {}).items())
            table.add_row(
                str(row.seq),
                f"+{offset:.1f}s",
                row.type,
                f"Q{numbers[row.question_id]}" if row.question_id in numbers else "-",
                payload[:60],
            )
        console.print(table)

        counts: dict[str, int] = {}
        for row in session.scalars(select(Event).where(Event.attempt_id == attempt_id)):
            counts[row.type] = counts.get(row.type, 0) + 1
        console.print(
            "\nTotals: " + ", ".join(f"{kind} {count}" for kind, count in sorted(counts.items()))
        )
    finally:
        session.close()


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind address; the default is reachable on the LAN."),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes."),
) -> None:
    """Run the API server."""
    import uvicorn

    load_dotenv()
    uvicorn.run("etap.api.app:app", host=host, port=port, reload=reload)


@app.command()
def profiles() -> None:
    """List the built-in exam profiles."""
    table = Table(title="Exam profiles")
    for column in ("id", "name", "duration", "sections", "sectional lock", "calculator"):
        table.add_column(column)

    for item in BUILTIN_PROFILES.values():
        table.add_row(
            item.id,
            item.name,
            f"{item.total_duration_min} min",
            ", ".join(section.name for section in item.sections) or "-",
            "yes" if item.sectional_lock else "no",
            "yes" if item.calculator else "no",
        )
    console.print(table)


def _report(paper: ParsedPaper, destination: Path) -> None:
    stats = paper.stats
    scanned = sum(1 for page in paper.pages if page.route is ExtractionRoute.VISION)

    summary = Table(title=f"{paper.title}", show_header=False)
    summary.add_column("field", style="bold")
    summary.add_column("value")
    summary.add_row("Pages", f"{stats.page_count} ({scanned} without a text layer)")
    summary.add_row("Questions", str(stats.question_count))
    summary.add_row("Sections", ", ".join(paper.sections) or "none detected")
    summary.add_row("With figures", str(stats.questions_with_figures))
    summary.add_row("From vision", str(stats.questions_from_vision))
    summary.add_row("Missing an answer", str(stats.questions_missing_key))
    summary.add_row("Mean confidence", f"{stats.mean_confidence:.2f}")
    summary.add_row("Needs review", str(stats.low_confidence_count))
    summary.add_row("Output", str(destination))
    console.print(summary)

    if paper.warnings:
        console.print("\n[bold yellow]Paper warnings[/bold yellow]")
        for warning in paper.warnings:
            console.print(f"  • {warning}")

    flagged = sorted(
        (question for question in paper.questions if question.notes),
        key=lambda question: question.confidence,
    )
    if flagged:
        console.print(f"\n[bold yellow]Questions needing attention ({len(flagged)})[/bold yellow]")
        for question in flagged[:20]:
            console.print(f"  [bold]Q{question.number}[/bold] (confidence {question.confidence:.2f})")
            for note in question.notes:
                console.print(f"      - {note}")
        if len(flagged) > 20:
            console.print(f"  ... and {len(flagged) - 20} more; see paper.json")


if __name__ == "__main__":
    app()

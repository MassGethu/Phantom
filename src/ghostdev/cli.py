"""Command entry points."""

import typer

app = typer.Typer(help="Find the hidden assumptions haunting repository setup.")


@app.command()
def haunt() -> None:
    typer.echo("not implemented yet")


@app.command()
def bisect() -> None:
    typer.echo("not implemented yet")


@app.command()
def exorcise() -> None:
    typer.echo("not implemented yet")


@app.command()
def doctor() -> None:
    typer.echo("not implemented yet")

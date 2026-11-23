"""Stateful terminal prompts with back navigation and a plain numbered fallback."""
from contextvars import ContextVar
import sys
from typing import Any

import typer


class Back(Exception):
    pass


class Wizard:
    def __init__(self, rich: bool):
        self.rich = rich
        self.answers: list[tuple[str, str, Any]] = []
        self.cursor = 0
        self.defaults: dict[tuple[str, str], Any] = {}

    def rewind(self) -> None:
        self.answers = self.answers[:max(0, self.cursor - 1)]
        self.cursor = 0

    def ask(self, kind: str, label: str, default: Any = None, choices: dict[str, str] | None = None) -> Any:
        if self.cursor < len(self.answers):
            old_kind, old_label, value = self.answers[self.cursor]
            if (old_kind, old_label) == (kind, label) and (choices is None or (all(key in choices for key in value) if kind == "checkbox" else value in choices)):
                self.cursor += 1
                return value
            self.answers = self.answers[:self.cursor]
        if self.rich:
            from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
            import questionary
            bindings = KeyBindings()
            @bindings.add("c-u", eager=True)
            def back(event):
                event.app.exit(exception=Back())
            if kind == "select":
                question = questionary.select(label, choices=[questionary.Choice(text, value=key) for key, text in (choices or {}).items()], default=default)
            elif kind == "confirm":
                question = questionary.confirm(label, default=bool(default))
            elif kind == "checkbox":
                question = questionary.checkbox(label, choices=[questionary.Choice(text, value=key, checked=key in default) for key, text in (choices or {}).items()])
            else:
                question = questionary.text(label, default=str(default or ""))
            question.application.key_bindings = merge_key_bindings([item for item in [question.application.key_bindings, bindings] if item is not None])
            value = question.unsafe_ask()
        elif kind == "confirm":
            value = typer.confirm(label, default=bool(default))
        else:
            if choices:
                for index, (key, text) in enumerate(choices.items(), 1):
                    typer.echo(f"  [{index}] {text} ({key})")
            value = typer.prompt(label + " (:back returns to the previous question)", default=default or "")
            if value == ":back":
                raise Back()
        self.defaults[(kind, label)] = value
        self.answers.append((kind, label, value))
        self.cursor += 1
        return value


active: ContextVar[Wizard | None] = ContextVar("ship_wizard", default=None)


def prompt(label: str, **kwargs: Any) -> Any:
    wizard = active.get()
    return wizard.ask("text", label, kwargs.get("default", "")) if wizard else typer.prompt(label, **kwargs)


def confirm(label: str, **kwargs: Any) -> bool:
    wizard = active.get()
    return bool(wizard.ask("confirm", label, kwargs.get("default", False))) if wizard else typer.confirm(label, **kwargs)


def terminal_wizard() -> Wizard:
    return Wizard(sys.stdin.isatty() and sys.stdout.isatty())

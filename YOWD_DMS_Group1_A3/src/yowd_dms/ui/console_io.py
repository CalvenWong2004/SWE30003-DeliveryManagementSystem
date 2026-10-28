"""Low-level console input and output with validation and cancelling.

Every prompt names the field, shows the expected format, re-asks after an
invalid entry, and accepts ``q`` to cancel back to the previous menu.
"""

from __future__ import annotations

import getpass
import sys
from typing import Callable, TypeVar

from ..domain.common import DomainError

T = TypeVar("T")
WIDTH = 72


class Cancelled(Exception):
    """Raised when the user types ``q`` to abandon the current task."""


class ConsoleIO:
    """Wraps input/print; echoes typed input when stdin is not a terminal.

    The echo makes transcripts of scripted runs (used as execution evidence)
    read exactly like an interactive session.
    """

    def __init__(self) -> None:
        self.interactive = sys.stdin.isatty()

    # -- output ---------------------------------------------------------------
    def say(self, text: str = "") -> None:
        """Print a line."""
        print(text)

    def heading(self, title: str) -> None:
        """Print a screen title between rules."""
        print()
        print("=" * WIDTH)
        print(f" {title}")
        print("=" * WIDTH)

    def success(self, text: str) -> None:
        """Print a confirmation line."""
        print(f"  [OK] {text}")

    def error(self, text: str) -> None:
        """Print a validation or business-rule error."""
        print(f"  [!] {text}")

    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        """Print rows as aligned columns, or a note when there are none."""
        if not rows:
            print("  (none)")
            return
        widths = [max(len(str(c)) for c in col)
                  for col in zip(headers, *rows)]
        line = "  " + "  ".join(h.ljust(w) for h, w in zip(headers, widths))
        print(line)
        print("  " + "  ".join("-" * w for w in widths))
        for row in rows:
            print("  " + "  ".join(str(c).ljust(w)
                                   for c, w in zip(row, widths)))

    # -- input ----------------------------------------------------------------
    def _read(self, prompt: str, secret: bool = False) -> str:
        if self.interactive:
            try:
                return getpass.getpass(prompt) if secret else input(prompt)
            except (EOFError, KeyboardInterrupt):
                # Ctrl+D / Ctrl+Z+Enter / Ctrl+C: end the session cleanly.
                print()
                raise Cancelled("End of input.")
        print(prompt, end="")
        line = sys.stdin.readline()
        if not line:
            raise Cancelled("End of input.")
        value = line.rstrip("\n")
        print("*" * len(value) if secret else value)
        return value

    def ask(self, label: str, convert: Callable[[str], T] = str,
            hint: str = "", secret: bool = False,
            default: str | None = None, can_quit: bool = True) -> T:
        """Ask until ``convert`` accepts the answer; ``q`` cancels."""
        suffix = f" [{hint}]" if hint else ""
        if default:
            suffix += f" (Enter = {default})"
        while True:
            raw = self._read(f"  {label}{suffix}: ", secret).strip()
            if can_quit and raw.lower() == "q":
                raise Cancelled()
            if not raw and default is not None:
                raw = default
            try:
                return convert(raw)
            except (DomainError, ValueError) as exc:
                self.error(str(exc) or f"{label} is not valid.")

    def choose(self, label: str, options: dict[str, str]) -> str:
        """Ask for one of ``options`` (key -> description)."""
        for key, text in options.items():
            print(f"   {key}) {text}")

        def check(raw: str) -> str:
            key = raw.upper()
            if key not in {k.upper() for k in options}:
                raise ValueError(
                    f"Choose one of: {', '.join(options)}.")
            return key
        return self.ask(label, check,
                        can_quit="Q" not in {k.upper() for k in options})

    def confirm(self, question: str) -> bool:
        """Ask a yes/no question."""
        def check(raw: str) -> bool:
            if raw.lower() in ("y", "yes"):
                return True
            if raw.lower() in ("n", "no"):
                return False
            raise ValueError("Please answer Y or N.")
        return self.ask(question, check, "Y/N")

    def pause(self) -> None:
        """Wait for Enter before returning to a menu."""
        self._read("  Press Enter to continue...")


def non_blank(field: str, max_length: int = 80) -> Callable[[str], str]:
    """Return a converter that rejects blank or over-long text."""
    def convert(raw: str) -> str:
        if not raw:
            raise ValueError(f"{field} cannot be blank.")
        if len(raw) > max_length:
            raise ValueError(f"{field} must be at most {max_length} "
                             "characters.")
        return raw
    return convert


def password(raw: str) -> str:
    """Accept a password of at least eight characters."""
    if len(raw) < 8:
        raise ValueError("Password must be at least 8 characters.")
    return raw


def money(raw: str) -> float:
    """Convert text such as ``45.50`` or ``$45.50`` to a positive amount."""
    try:
        value = round(float(raw.replace("$", "").replace(",", "")), 2)
    except ValueError as exc:
        raise ValueError("Enter an amount such as 45.50.") from exc
    if value <= 0:
        raise ValueError("Amount must be more than $0.")
    return value


def weight(raw: str) -> float:
    """Convert text to a weight in kilograms."""
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError("Weight must be a number of kg, e.g. 2.5.") from exc

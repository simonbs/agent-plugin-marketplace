#!/usr/bin/env python3
"""Draw one unique CSV winner, record it, and print a privacy-safe announcement."""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path
from typing import Any


NAME_ALIASES = ("name", "full name", "fullname", "participant name", "entrant name")
EMAIL_ALIASES = ("email", "e-mail", "email address", "e-mail address")


class SafeError(Exception):
    """An error message that contains no entrant data."""


def normalize_header(value: str) -> str:
    return " ".join(value.strip().casefold().replace("_", " ").replace("-", " ").split())


def resolve_column(fieldnames: list[str], requested: str | None, aliases: tuple[str, ...], kind: str) -> str:
    normalized = {normalize_header(field): field for field in fieldnames}
    if requested is not None:
        match = normalized.get(normalize_header(requested))
        if match is None:
            raise SafeError(f"The requested {kind} column was not found. Available headers: {', '.join(fieldnames)}")
        return match
    for alias in aliases:
        match = normalized.get(normalize_header(alias))
        if match is not None:
            return match
    raise SafeError(f"Could not identify the {kind} column. Available headers: {', '.join(fieldnames)}")


def valid_email_shape(email: str) -> bool:
    if email.count("@") != 1 or any(character.isspace() for character in email):
        return False
    local, domain = email.rsplit("@", 1)
    return bool(local and "." in domain and all(domain.split(".")))


def masked_email(email: str) -> str:
    """Keep a short identifying prefix while concealing the rest of the address."""
    local, domain = email.rsplit("@", 1)
    domain_parts = domain.split(".")

    def mask_part(value: str, visible_limit: int) -> str:
        visible_count = min(visible_limit, max(1, len(value) - 1))
        return f"{value[:visible_count]}***"

    masked_local = mask_part(local, 4)
    masked_domain = ".".join(
        [*(mask_part(part, 3) for part in domain_parts[:-1]), domain_parts[-1]]
    )
    return f"{masked_local}@{masked_domain}"


def announcement_name(name: str) -> str:
    """Keep the full name while preventing embedded whitespace from forging output lines."""
    return " ".join(name.split())


def load_entrants(csv_path: Path, name_column: str | None, email_column: str | None) -> list[dict[str, str]]:
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            sample = handle.read(8192)
            handle.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(handle, dialect=dialect)
            if not reader.fieldnames:
                raise SafeError("The CSV has no header row.")
            fields = [field for field in reader.fieldnames if field is not None and field.strip()]
            if not fields:
                raise SafeError("The CSV has no usable headers.")
            name_key = resolve_column(fields, name_column, NAME_ALIASES, "name")
            email_key = resolve_column(fields, email_column, EMAIL_ALIASES, "email")

            entrants_by_email: dict[str, dict[str, str]] = {}
            invalid_rows = 0
            for row in reader:
                name = (row.get(name_key) or "").strip()
                email = (row.get(email_key) or "").strip()
                if not name or not valid_email_shape(email):
                    invalid_rows += 1
                    continue
                identity = email.casefold()
                entrants_by_email.setdefault(identity, {"name": name, "email": email, "identity": identity})
    except (OSError, UnicodeError) as error:
        raise SafeError(f"Could not read the CSV: {error.__class__.__name__}.") from None

    if invalid_rows:
        raise SafeError(f"The CSV contains {invalid_rows} row(s) with a missing name or invalid email address; no winner was drawn.")
    if not entrants_by_email:
        raise SafeError("The CSV contains no eligible entrants.")
    return list(entrants_by_email.values())


def identity_hash(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def state_path(csv_path: Path, state_dir: Path) -> Path:
    path_key = hashlib.sha256(str(csv_path.resolve()).encode("utf-8")).hexdigest()
    return state_dir / f"{path_key}.json"


def load_state(handle: Any) -> dict[str, Any]:
    handle.seek(0)
    raw = handle.read()
    if not raw:
        return {"selected": [], "last": None}
    try:
        state = json.loads(raw)
        selected = state.get("selected", [])
        last = state.get("last")
        if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
            raise ValueError
        if last is not None and not isinstance(last, str):
            raise ValueError
        return {"selected": selected, "last": last}
    except (json.JSONDecodeError, ValueError, AttributeError):
        raise SafeError("The private draw history is damaged; no winner was drawn.") from None


def save_state(handle: Any, state: dict[str, Any]) -> None:
    handle.seek(0)
    json.dump(state, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.truncate()
    handle.flush()
    os.fsync(handle.fileno())


def record_winner(output_path: Path, winner: dict[str, str]) -> None:
    winners: list[dict[str, str]] = []
    if output_path.exists():
        try:
            with output_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames != ["name", "email"]:
                    raise SafeError("The existing winners.csv is not a valid winner list; no winner was drawn.")
                for row in reader:
                    if row.get(None) or not row.get("name") or not valid_email_shape(row.get("email") or ""):
                        raise SafeError("The existing winners.csv is not a valid winner list; no winner was drawn.")
                    winners.append({"name": row["name"], "email": row["email"]})
        except (OSError, UnicodeError, csv.Error):
            raise SafeError("The existing winners.csv is not a readable CSV winner list; no winner was drawn.") from None

    winners.append({"name": winner["name"], "email": winner["email"]})
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=".winners-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            os.chmod(temporary_path, 0o600)
            writer = csv.DictWriter(handle, fieldnames=["name", "email"], lineterminator="\n")
            writer.writeheader()
            writer.writerows(winners)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output_path)
    except (OSError, UnicodeError):
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise SafeError("Could not update winners.csv; no winner was drawn.") from None


def draw(
    entrants: list[dict[str, str]], history_path: Path, output_path: Path, reset: bool
) -> dict[str, str]:
    history_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(history_path.parent, 0o700)
    except OSError:
        pass

    descriptor = os.open(history_path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "r+", encoding="utf-8") as handle:
        try:
            os.chmod(history_path, 0o600)
        except OSError:
            pass
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        state = load_state(handle)
        prior_selected = set(state["selected"])
        last = state["last"]
        if reset:
            prior_selected = {last} if last and len(entrants) > 1 else set()

        eligible = [entrant for entrant in entrants if identity_hash(entrant["identity"]) not in prior_selected]
        if not eligible:
            raise SafeError("Every eligible entrant has already won; no winner was drawn.")

        winner = secrets.choice(eligible)
        winner_hash = identity_hash(winner["identity"])
        selected = [winner_hash] if reset else list(dict.fromkeys([*state["selected"], winner_hash]))
        record_winner(output_path, winner)
        save_state(handle, {"selected": selected, "last": winner_hash})
        return winner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="raffle", description="Draw one unique winner from a private CSV roster.")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--name-column")
    parser.add_argument("--email-column")
    parser.add_argument("--reset", action="store_true", help="Start a new draw cycle; use only on explicit request.")
    parser.add_argument("--state-dir", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    csv_path = args.csv_path.expanduser()
    if not csv_path.is_file():
        print("Error: The supplied CSV path is not a readable file.", file=sys.stderr)
        return 2
    state_dir = args.state_dir or (Path.home() / ".codex" / "state" / "raffle")
    try:
        entrants = load_entrants(csv_path, args.name_column, args.email_column)
        winner = draw(entrants, state_path(csv_path, state_dir), Path.cwd() / "winners.csv", args.reset)
    except SafeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    print(f"Winner: {announcement_name(winner['name'])} ({masked_email(winner['email'])})")
    print("Winner recorded in winners.csv.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

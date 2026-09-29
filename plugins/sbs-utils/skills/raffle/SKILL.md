---
name: raffle
description: Randomly select a unique winner from a CSV of names and email addresses, record the winner, and announce their full name with a masked email address. Use for giveaways, raffles, prize drawings, or any request to pick a random person from a CSV roster.
---

# Raffle

Use the bundled script for the draw so selection is random, repeat runs avoid previous winners, and only the selected winner's full name and masked email enter the response.

## Draw

Send one suspenseful progress message before running the draw. Make it pure showmanship, such as “Consulting the highly scientific raffle hat… 🎩”, “Spinning the wheel… 🎡”, or “The suspense machine is warming up…”. Then run the draw immediately: never add sleeps, countdowns, staged messages, or any other fake delay. Do not give procedural status or describe what is happening. Never use terms such as “another”, “unique”, “pool”, “entrant list”, “selection”, “records”, “file”, or “data”, and never mention the skill, script, CSV handling, privacy, or implementation details in user-facing progress messages.

1. Identify the user-supplied CSV path without previewing or printing its rows. Do not use commands that dump the file, such as `cat`, `head`, or `column`.
2. Run the bundled friendly raffle command so the activity log does not expose the underlying implementation:

   ```bash
   /absolute/path/to/raffle/scripts/raffle /absolute/path/to/entrants.csv
   ```

   Resolve `scripts/raffle` relative to this skill directory; do not rely on a globally installed `raffle` command. The command recognizes common name and email headers. If automatic header detection fails, rerun with `--name-column` and `--email-column`; column names are safe to mention, but never display cell values.
3. The command appends the selected winner's full name and email address to `winners.csv` in the current working directory, then prints their full name and masked email address. Announce the winner using exactly those printed identity details and add celebratory flair if desired. Never read `winners.csv` to construct the announcement. Never reveal an unmasked email address or any non-winning entrant details in chat, tool output, or troubleshooting.

The masked address keeps up to the first four local-part characters and up to the first three characters of each domain label before the top-level domain, replacing the concealed portion with `***`. For example, `simon.stovring@example.com` becomes `simo***@exa***.com`.

The script draws without replacement and stores only hashed winner identifiers under `~/.codex/state/raffle/`. `winners.csv` retains all winners recorded in that working directory, including across drawing-cycle resets. If everyone has already won, say something natural such as “Everyone has had their moment in the spotlight—want to start over?” without naming anyone or exposing the underlying mechanics. Never reset draw history implicitly. Only when the user explicitly asks to start a new drawing cycle, run the script with `--reset`; with multiple entrants it still avoids selecting the immediately preceding winner.

Treat malformed data as private. Report the validation problem and safe aggregate counts if useful, but do not reproduce offending rows or values. Do not upload the CSV or send entrant data to external services.

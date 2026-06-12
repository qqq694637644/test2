"""Command line interface for the terminal Tank Battle game."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TextIO

from tank_battle.engine import Action, Game, GameResult, StepReport

HELP_TEXT = """Commands:
  W/A/S/D or up/down/left/right  move and face that direction
  F or fire                      fire in the current direction
  Enter or wait                  skip a turn
  Q or quit                      quit the game
"""


def parse_command(raw: str) -> Action:
    """Parse one interactive command into an engine action."""

    return Action.from_value(raw)


def load_game(level_file: str | None) -> Game:
    """Load a game from a level file, or return the bundled default level."""

    if level_file is None:
        return Game.default()
    return Game.from_ascii(Path(level_file).read_text(encoding="utf-8"))


def print_turn(output: TextIO, game: Game, report: StepReport | None = None) -> None:
    """Write the current board and optional report to the output stream."""

    output.write(f"\nTurn: {game.turn}  Result: {game.result.value}\n")
    output.write(game.render())
    output.write("\n")
    if report is not None:
        for event in report.events:
            output.write(f"- {event}\n")


def run_scripted_actions(game: Game, actions_text: str, output: TextIO) -> GameResult:
    """Run comma-separated actions without interactive input."""

    report: StepReport | None = None
    for raw_action in actions_text.split(","):
        action = parse_command(raw_action)
        report = game.step(action)
        print_turn(output, game, report)
        if game.result is not GameResult.ONGOING:
            break
    if report is None:
        print_turn(output, game)
    return game.result


def play_interactive(
    game: Game,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    max_turns: int,
) -> GameResult:
    """Run an interactive terminal session."""

    output_stream.write("Tank Battle\n")
    output_stream.write(HELP_TEXT)
    report: StepReport | None = None

    while game.result is GameResult.ONGOING and game.turn < max_turns:
        print_turn(output_stream, game, report)
        output_stream.write("> ")
        output_stream.flush()
        command = input_stream.readline()
        if command == "":
            report = game.step(Action.QUIT)
            break
        try:
            action = parse_command(command)
        except ValueError as exc:
            output_stream.write(f"Invalid command: {exc}\n")
            report = None
            continue
        report = game.step(action)

    if game.result is GameResult.ONGOING and game.turn >= max_turns:
        output_stream.write(f"Reached max turn limit ({max_turns}); ending game.\n")

    print_turn(output_stream, game, report)
    return game.result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Play a terminal Tank Battle game.")
    parser.add_argument("--level-file", help="UTF-8 ASCII level file to load instead of the default level")
    parser.add_argument(
        "--actions",
        help="comma-separated scripted actions for non-interactive runs, e.g. right,fire,wait",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=200,
        help="maximum interactive turns before the game ends",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_turns < 1:
        raise SystemExit("--max-turns must be at least 1")

    game = load_game(args.level_file)
    if args.actions is not None:
        run_scripted_actions(game, args.actions, sys.stdout)
    else:
        play_interactive(
            game,
            input_stream=sys.stdin,
            output_stream=sys.stdout,
            max_turns=args.max_turns,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

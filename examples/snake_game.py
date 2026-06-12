"""Standalone command-line Snake example.

This file deliberately lives outside the installable ``vm_test_template`` package.
Run it directly from a checkout with ``python examples/snake_game.py``.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TextIO

Point = tuple[int, int]
Direction = str

DIRECTIONS: dict[Direction, Point] = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}
OPPOSITE_DIRECTIONS: dict[Direction, Direction] = {
    "up": "down",
    "down": "up",
    "left": "right",
    "right": "left",
}
KEY_DIRECTIONS: dict[str, Direction] = {
    "w": "up",
    "k": "up",
    "\x1b[A": "up",
    "s": "down",
    "j": "down",
    "\x1b[B": "down",
    "a": "left",
    "h": "left",
    "\x1b[D": "left",
    "d": "right",
    "l": "right",
    "\x1b[C": "right",
}
ESCAPE_SEQUENCE_TIMEOUT_SECONDS = 0.03


@dataclass(frozen=True)
class GameConfig:
    """Settings that define a Snake board."""

    width: int = 30
    height: int = 15
    initial_length: int = 3

    def validate(self) -> None:
        if self.width < 10:
            raise ValueError("width must be at least 10")
        if self.height < 6:
            raise ValueError("height must be at least 6")
        if self.initial_length < 2:
            raise ValueError("initial length must be at least 2")

        max_centered_length = self.width // 2 + 1
        if self.initial_length > max_centered_length:
            raise ValueError(
                "initial length must fit on the board when centered "
                f"(max {max_centered_length} for width {self.width})"
            )


@dataclass(frozen=True)
class StepResult:
    """Result of one game tick."""

    ate_food: bool = False
    collision: bool = False
    won: bool = False
    reason: str = ""


@dataclass
class SnakeGame:
    """Pure game-state model for Snake."""

    config: GameConfig = field(default_factory=GameConfig)
    rng: random.Random = field(default_factory=random.Random)
    snake: deque[Point] = field(init=False)
    direction: Direction = "right"
    food: Point | None = None
    score: int = 0
    game_over: bool = False
    won: bool = False

    def __post_init__(self) -> None:
        self.config.validate()
        center_x = self.config.width // 2
        center_y = self.config.height // 2
        self.snake = deque(
            (center_x - offset, center_y) for offset in range(self.config.initial_length)
        )
        self._spawn_food()

    @classmethod
    def create(cls, config: GameConfig | None = None, seed: int | None = None) -> SnakeGame:
        """Create a new game, optionally with deterministic random food placement."""

        return cls(config=config or GameConfig(), rng=random.Random(seed))

    @property
    def head(self) -> Point:
        return self.snake[0]

    def change_direction(self, direction: Direction) -> bool:
        """Change movement direction, rejecting direct reversals.

        Returns ``True`` when the direction is accepted and ``False`` when the
        request is a no-op reverse turn that would immediately collide.
        """

        if direction not in DIRECTIONS:
            raise ValueError(f"unknown direction: {direction}")
        if len(self.snake) > 1 and direction == OPPOSITE_DIRECTIONS[self.direction]:
            return False
        self.direction = direction
        return True

    def step(self) -> StepResult:
        """Advance the game by one tick."""

        if self.game_over:
            return StepResult(collision=not self.won, won=self.won, reason="game_over")

        dx, dy = DIRECTIONS[self.direction]
        new_head = (self.head[0] + dx, self.head[1] + dy)
        ate_food = new_head == self.food

        if not self._inside_board(new_head):
            self.game_over = True
            return StepResult(collision=True, reason="wall")

        occupied = set(self.snake)
        if not ate_food:
            occupied.remove(self.snake[-1])
        if new_head in occupied:
            self.game_over = True
            return StepResult(collision=True, reason="self")

        self.snake.appendleft(new_head)
        if ate_food:
            self.score += 1
            if len(self.snake) == self.config.width * self.config.height:
                self.food = None
                self.won = True
                self.game_over = True
                return StepResult(ate_food=True, won=True, reason="filled_board")
            self._spawn_food()
            return StepResult(ate_food=True)

        self.snake.pop()
        return StepResult()

    def _inside_board(self, point: Point) -> bool:
        x, y = point
        return 0 <= x < self.config.width and 0 <= y < self.config.height

    def _spawn_food(self) -> None:
        snake_cells = set(self.snake)
        open_cells = [
            (x, y)
            for y in range(self.config.height)
            for x in range(self.config.width)
            if (x, y) not in snake_cells
        ]
        if not open_cells:
            self.food = None
            self.won = True
            self.game_over = True
            return
        self.food = self.rng.choice(open_cells)


class KeyboardReader:
    """Non-blocking terminal key reader for POSIX and Windows terminals."""

    def __init__(self, input_stream: TextIO | None = None) -> None:
        self.input_stream = input_stream if input_stream is not None else sys.stdin
        self._old_terminal_settings: list[object] | None = None
        self._fd: int | None = None

    def __enter__(self) -> KeyboardReader:
        if os.name == "nt" or not self.input_stream.isatty():
            return self

        import termios
        import tty

        self._fd = self.input_stream.fileno()
        self._old_terminal_settings = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)
        return self

    def __exit__(self, *_exc_info: object) -> None:
        if self._old_terminal_settings is None or self._fd is None:
            return

        import termios

        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_terminal_settings)

    def read_key(self) -> str | None:
        if os.name == "nt":
            return self._read_windows_key()
        return self._read_posix_key()

    def _read_windows_key(self) -> str | None:
        import msvcrt

        if not msvcrt.kbhit():
            return None
        key = msvcrt.getwch()
        if key in {"\x00", "\xe0"}:
            code = msvcrt.getwch()
            return {
                "H": "\x1b[A",
                "P": "\x1b[B",
                "K": "\x1b[D",
                "M": "\x1b[C",
            }.get(code)
        return key

    def _read_posix_key(self) -> str | None:
        if not self.input_stream.isatty():
            return None

        import select

        readable, _, _ = select.select([self.input_stream], [], [], 0)
        if not readable:
            return None

        key = self.input_stream.read(1)
        if key != "\x1b":
            return key

        sequence = [key]
        for _ in range(2):
            readable, _, _ = select.select(
                [self.input_stream],
                [],
                [],
                ESCAPE_SEQUENCE_TIMEOUT_SECONDS,
            )
            if not readable:
                break
            sequence.append(self.input_stream.read(1))
        return "".join(sequence)


def direction_from_key(key: str) -> Direction | None:
    """Map keyboard input to a movement direction."""

    return KEY_DIRECTIONS.get(key) or KEY_DIRECTIONS.get(key.lower())


def render_board(game: SnakeGame, *, paused: bool = False, message: str = "") -> str:
    """Render the current board as plain terminal text."""

    snake_cells = set(game.snake)
    head = game.head
    lines = ["+" + "-" * game.config.width + "+"]
    for y in range(game.config.height):
        row = []
        for x in range(game.config.width):
            point = (x, y)
            if point == head:
                row.append("@")
            elif point in snake_cells:
                row.append("o")
            elif point == game.food:
                row.append("*")
            else:
                row.append(" ")
        lines.append("|" + "".join(row) + "|")
    lines.append("+" + "-" * game.config.width + "+")

    status = f"Score: {game.score}  Controls: WASD/Arrows move, P pause, Q quit"
    if paused:
        status += "  [PAUSED]"
    lines.append(status)
    if message:
        lines.append(message)
    return "\n".join(lines) + "\n"


def run_terminal_game(
    config: GameConfig,
    *,
    speed: float,
    seed: int | None = None,
    output: TextIO | None = None,
) -> int:
    """Run the interactive terminal UI."""

    if speed <= 0:
        raise ValueError("speed must be greater than 0")

    output = output if output is not None else sys.stdout
    game = SnakeGame.create(config=config, seed=seed)
    frame_delay_seconds = 1 / speed
    last_tick = time.monotonic()
    paused = False
    final_message = "Game over."

    output.write("\x1b[?25l\x1b[2J")
    output.flush()
    try:
        with KeyboardReader() as keyboard:
            while not game.game_over:
                key = keyboard.read_key()
                if key:
                    normalized = key.lower()
                    if normalized == "q":
                        final_message = "Quit."
                        break
                    if normalized == "p":
                        paused = not paused
                    direction = direction_from_key(key)
                    if direction is not None:
                        game.change_direction(direction)

                now = time.monotonic()
                if not paused and now - last_tick >= frame_delay_seconds:
                    result = game.step()
                    last_tick = now
                    if result.won:
                        final_message = "You win!"
                    elif result.collision:
                        final_message = f"Game over: hit {result.reason}."

                output.write("\x1b[H")
                output.write(
                    render_board(
                        game,
                        paused=paused,
                        message=final_message if game.game_over else "",
                    )
                )
                output.flush()
                time.sleep(0.01)
    finally:
        output.write("\x1b[?25h")
        output.flush()

    output.write("\x1b[H")
    output.write(render_board(game, paused=False, message=final_message))
    output.flush()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Play Snake in your terminal.")
    parser.add_argument("--width", type=int, default=30, help="board width, minimum 10")
    parser.add_argument("--height", type=int, default=15, help="board height, minimum 6")
    parser.add_argument("--speed", type=float, default=8.0, help="moves per second")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="deterministic food seed for debugging",
    )
    return parser.parse_args(argv)


def terminal_size_hint(config: GameConfig, columns: int, lines: int) -> str:
    """Return a warning when the terminal is smaller than the board needs."""

    needed_columns = config.width + 2
    needed_lines = config.height + 4
    if columns >= needed_columns and lines >= needed_lines:
        return ""
    return (
        f"terminal is {columns}x{lines}; "
        f"recommended minimum is {needed_columns}x{needed_lines}"
    )


def _terminal_size_hint(config: GameConfig, output: TextIO | None = None) -> str:
    output = output if output is not None else sys.stdout
    size = os.get_terminal_size(output.fileno())
    return terminal_size_hint(config, columns=size.columns, lines=size.lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = GameConfig(width=args.width, height=args.height)
    try:
        config.validate()
        if args.speed <= 0:
            raise ValueError("speed must be greater than 0")
    except ValueError as exc:
        print(f"snake example: {exc}", file=sys.stderr)
        return 2

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("snake example requires an interactive terminal (TTY).", file=sys.stderr)
        return 2

    try:
        hint = _terminal_size_hint(config)
    except OSError as exc:
        print(f"snake example: cannot read terminal size: {exc}", file=sys.stderr)
        return 2

    if hint:
        print(f"snake example: warning: {hint}", file=sys.stderr)

    try:
        return run_terminal_game(config, speed=args.speed, seed=args.seed)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

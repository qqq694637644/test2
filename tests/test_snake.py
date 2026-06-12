"""Tests for the standalone command-line Snake example."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections import deque
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAKE_EXAMPLE_PATH = REPO_ROOT / "examples" / "snake_game.py"

spec = importlib.util.spec_from_file_location("snake_game_example", SNAKE_EXAMPLE_PATH)
assert spec is not None
assert spec.loader is not None
snake = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = snake
spec.loader.exec_module(snake)


class FakeStream:
    def __init__(self, text: str = "", *, tty: bool = True) -> None:
        self._text = list(text)
        self._tty = tty
        self.written = ""

    def isatty(self) -> bool:
        return self._tty

    def fileno(self) -> int:
        return 0

    def read(self, size: int = 1) -> str:
        output = "".join(self._text[:size])
        del self._text[:size]
        return output

    def write(self, text: str) -> int:
        self.written += text
        return len(text)

    def flush(self) -> None:
        return None

    @property
    def has_input(self) -> bool:
        return bool(self._text)


def test_example_is_not_part_of_public_package_interface():
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert not (REPO_ROOT / "src" / "vm_test_template" / "snake.py").exists()
    assert "snake-game" not in pyproject
    assert "vm_test_template.snake" not in pyproject
    assert "命令行贪吃蛇" not in readme


def test_new_game_spawns_food_outside_snake():
    game = snake.SnakeGame.create(snake.GameConfig(width=10, height=6), seed=1)

    assert game.food is not None
    assert game.food not in game.snake


def test_initial_length_must_fit_centered_snake_on_board():
    valid_game = snake.SnakeGame.create(
        snake.GameConfig(width=10, height=6, initial_length=6),
        seed=1,
    )

    assert all(0 <= x < 10 and 0 <= y < 6 for x, y in valid_game.snake)
    with pytest.raises(ValueError, match="initial length must fit"):
        snake.SnakeGame.create(snake.GameConfig(width=10, height=6, initial_length=7))


def test_snake_grows_and_scores_when_eating_food():
    game = snake.SnakeGame.create(snake.GameConfig(width=10, height=6), seed=1)
    game.food = (game.head[0] + 1, game.head[1])
    old_length = len(game.snake)

    result = game.step()

    assert result.ate_food is True
    assert result.collision is False
    assert game.score == 1
    assert len(game.snake) == old_length + 1
    assert game.head == (6, 3)
    assert game.food not in game.snake


def test_direct_reverse_turn_is_rejected():
    game = snake.SnakeGame.create(snake.GameConfig(width=10, height=6), seed=1)

    assert game.change_direction("left") is False
    assert game.direction == "right"

    assert game.change_direction("down") is True
    assert game.direction == "down"


def test_wall_collision_ends_game():
    game = snake.SnakeGame.create(snake.GameConfig(width=10, height=6), seed=1)
    game.snake = deque([(9, 3), (8, 3), (7, 3)])
    game.direction = "right"
    game.food = (0, 0)

    result = game.step()

    assert result.collision is True
    assert result.reason == "wall"
    assert game.game_over is True


def test_self_collision_ends_game():
    game = snake.SnakeGame.create(snake.GameConfig(width=10, height=6), seed=1)
    game.snake = deque([(4, 3), (4, 4), (3, 4), (3, 3), (3, 2), (4, 2)])
    game.direction = "left"
    game.food = (0, 0)

    result = game.step()

    assert result.collision is True
    assert result.reason == "self"
    assert game.game_over is True


def test_eating_last_open_cell_wins_game():
    config = snake.GameConfig(width=10, height=6)
    game = snake.SnakeGame.create(config, seed=1)
    food = (9, 5)
    cells = [(x, y) for y in range(config.height) for x in range(config.width)]
    game.snake = deque([(8, 5), *(cell for cell in cells if cell not in {(8, 5), food})])
    game.direction = "right"
    game.food = food

    result = game.step()

    assert result.ate_food is True
    assert result.won is True
    assert result.reason == "filled_board"
    assert game.won is True
    assert game.game_over is True
    assert game.food is None
    assert len(game.snake) == config.width * config.height


def test_key_mapping_and_rendering():
    game = snake.SnakeGame.create(snake.GameConfig(width=10, height=6), seed=1)

    assert snake.direction_from_key("w") == "up"
    assert snake.direction_from_key("\x1b[B") == "down"
    board = snake.render_board(game)

    assert "@" in board
    assert "*" in board
    assert "Score: 0" in board


def test_posix_arrow_key_sequence_waits_for_escape_suffix(monkeypatch):
    stream = FakeStream("\x1b[A")
    timeouts: list[float] = []

    def fake_select(read_list, _write_list, _error_list, timeout):
        timeouts.append(timeout)
        return (read_list, [], []) if stream.has_input else ([], [], [])

    import select

    monkeypatch.setattr(select, "select", fake_select)

    assert snake.KeyboardReader(stream)._read_posix_key() == "\x1b[A"
    assert timeouts == [0, snake.ESCAPE_SEQUENCE_TIMEOUT_SECONDS, 0.03]


def test_main_returns_error_for_invalid_arguments(monkeypatch):
    stderr = FakeStream()
    monkeypatch.setattr(snake.sys, "stderr", stderr)

    assert snake.main(["--width", "9"]) == 2
    assert "width must be at least 10" in stderr.written


def test_main_rejects_non_tty(monkeypatch):
    stdin = FakeStream(tty=False)
    stdout = FakeStream(tty=True)
    stderr = FakeStream(tty=True)
    monkeypatch.setattr(snake.sys, "stdin", stdin)
    monkeypatch.setattr(snake.sys, "stdout", stdout)
    monkeypatch.setattr(snake.sys, "stderr", stderr)

    assert snake.main([]) == 2
    assert "requires an interactive terminal" in stderr.written


def test_snake_example_help_command_runs():
    result = subprocess.run(
        [sys.executable, str(SNAKE_EXAMPLE_PATH), "--help"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Play Snake in your terminal" in result.stdout

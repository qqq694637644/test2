"""Tests for the command-line Snake game."""

from __future__ import annotations

import os
import sys
from collections import deque

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vm_test_template.snake import (  # noqa: E402
    GameConfig,
    SnakeGame,
    direction_from_key,
    render_board,
)


def test_new_game_spawns_food_outside_snake():
    game = SnakeGame.create(GameConfig(width=10, height=6), seed=1)

    assert game.food is not None
    assert game.food not in game.snake


def test_snake_grows_and_scores_when_eating_food():
    game = SnakeGame.create(GameConfig(width=10, height=6), seed=1)
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
    game = SnakeGame.create(GameConfig(width=10, height=6), seed=1)

    assert game.change_direction("left") is False
    assert game.direction == "right"

    assert game.change_direction("down") is True
    assert game.direction == "down"


def test_wall_collision_ends_game():
    game = SnakeGame.create(GameConfig(width=10, height=6), seed=1)
    game.snake = deque([(9, 3), (8, 3), (7, 3)])
    game.direction = "right"
    game.food = (0, 0)

    result = game.step()

    assert result.collision is True
    assert result.reason == "wall"
    assert game.game_over is True


def test_self_collision_ends_game():
    game = SnakeGame.create(GameConfig(width=10, height=6), seed=1)
    game.snake = deque([(4, 3), (4, 4), (3, 4), (3, 3), (3, 2), (4, 2)])
    game.direction = "left"
    game.food = (0, 0)

    result = game.step()

    assert result.collision is True
    assert result.reason == "self"
    assert game.game_over is True


def test_key_mapping_and_rendering():
    game = SnakeGame.create(GameConfig(width=10, height=6), seed=1)

    assert direction_from_key("w") == "up"
    assert direction_from_key("\x1b[B") == "down"
    board = render_board(game)

    assert "@" in board
    assert "*" in board
    assert "Score: 0" in board

"""Tests for the terminal Tank Battle game."""

from __future__ import annotations

import os
import sys
from io import StringIO

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tank_battle import Action, Game, GameResult, Position  # noqa: E402
from tank_battle.cli import parse_command, run_scripted_actions  # noqa: E402


def test_level_parser_requires_one_player_and_rectangular_map():
    with pytest.raises(ValueError, match="exactly one player"):
        Game.from_ascii(
            """
            ###
            #E#
            ###
            """
        )

    with pytest.raises(ValueError, match="rectangular"):
        Game.from_ascii(
            """
            ####
            #P#
            ####
            """
        )


def test_player_movement_faces_direction_and_respects_walls():
    game = Game.from_ascii(
        """
        #####
        #P#E#
        #...#
        #####
        """
    )

    blocked = game.step("right")
    assert game.player.position == Position(1, 1)
    assert game.player.direction.value == "right"
    assert "player blocked by wall" in blocked.events

    moved = game.step("down")
    assert game.player.position == Position(2, 1)
    assert game.player.direction.value == "down"
    assert "player moved down" in moved.events


def test_player_fire_destroys_enemy_in_line_of_sight_and_wins():
    game = Game.from_ascii(
        """
        #######
        #>...E#
        #######
        """
    )

    report = game.step(Action.FIRE)

    assert report.result is GameResult.WIN
    assert game.enemies == {}
    assert "player hit enemy-1; hp=0" in report.events
    assert "enemy-1 destroyed" in report.events


def test_wall_blocks_player_fire():
    game = Game.from_ascii(
        """
        ########
        #>.#..E#
        ########
        """
    )

    report = game.step("fire")

    assert report.result is GameResult.ONGOING
    assert len(game.enemies) == 1
    assert "player fired and missed" in report.events


def test_enemy_ai_moves_deterministically_toward_player_when_not_aligned():
    game = Game.from_ascii(
        """
        #######
        #P....#
        #.....#
        #....E#
        #######
        """
    )

    game.step("wait")

    assert game.enemies["enemy-1"].position == Position(3, 4)


def test_enemy_line_of_sight_fires_until_player_loses():
    game = Game.from_ascii(
        """
        #######
        #E...P#
        #######
        """
    )

    first = game.step("wait")
    second = game.step("wait")
    third = game.step("wait")

    assert first.result is GameResult.ONGOING
    assert second.result is GameResult.ONGOING
    assert third.result is GameResult.LOSE
    assert game.player.hp == 0
    assert "enemy-1 hit player; hp=0" in third.events


def test_render_preserves_board_size_and_shows_tank_direction():
    game = Game.from_ascii(
        """
        #####
        #v.E#
        #####
        """
    )

    rendered = game.render().splitlines()

    assert len(rendered) == 3
    assert {len(row) for row in rendered} == {5}
    assert rendered[1][1] == "v"
    assert rendered[1][3] == "E"


def test_cli_command_parsing_and_scripted_actions():
    assert parse_command("w") is Action.MOVE_UP
    assert parse_command("fire") is Action.FIRE
    assert parse_command("") is Action.WAIT
    assert parse_command("q") is Action.QUIT

    with pytest.raises(ValueError, match="unknown action"):
        parse_command("bad-command")

    game = Game.from_ascii(
        """
        #######
        #>...E#
        #######
        """
    )
    output = StringIO()

    result = run_scripted_actions(game, "fire", output)

    assert result is GameResult.WIN
    assert "Result: win" in output.getvalue()

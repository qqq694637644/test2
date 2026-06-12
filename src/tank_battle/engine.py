"""Core game engine for a deterministic terminal Tank Battle game.

The engine is intentionally independent from terminal input/output so it can be
unit tested without timing, rendering, or keyboard dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from textwrap import dedent


class GameResult(str, Enum):
    """Current game state."""

    ONGOING = "ongoing"
    WIN = "win"
    LOSE = "lose"
    QUIT = "quit"


class Direction(str, Enum):
    """Cardinal tank directions."""

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"

    @property
    def delta(self) -> tuple[int, int]:
        match self:
            case Direction.UP:
                return (-1, 0)
            case Direction.DOWN:
                return (1, 0)
            case Direction.LEFT:
                return (0, -1)
            case Direction.RIGHT:
                return (0, 1)
        raise ValueError(f"unsupported direction: {self!r}")

    @property
    def glyph(self) -> str:
        match self:
            case Direction.UP:
                return "^"
            case Direction.DOWN:
                return "v"
            case Direction.LEFT:
                return "<"
            case Direction.RIGHT:
                return ">"
        raise ValueError(f"unsupported direction: {self!r}")


class Action(str, Enum):
    """Player actions accepted by :meth:`Game.step`."""

    MOVE_UP = "up"
    MOVE_DOWN = "down"
    MOVE_LEFT = "left"
    MOVE_RIGHT = "right"
    FIRE = "fire"
    WAIT = "wait"
    QUIT = "quit"

    @classmethod
    def from_value(cls, value: Action | str) -> Action:
        if isinstance(value, Action):
            return value
        normalized = str(value).strip().lower()
        aliases = {
            "": cls.WAIT,
            "w": cls.MOVE_UP,
            "up": cls.MOVE_UP,
            "s": cls.MOVE_DOWN,
            "down": cls.MOVE_DOWN,
            "a": cls.MOVE_LEFT,
            "left": cls.MOVE_LEFT,
            "d": cls.MOVE_RIGHT,
            "right": cls.MOVE_RIGHT,
            "f": cls.FIRE,
            "fire": cls.FIRE,
            "space": cls.FIRE,
            "wait": cls.WAIT,
            "q": cls.QUIT,
            "quit": cls.QUIT,
            "exit": cls.QUIT,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise ValueError(f"unknown action: {value!r}") from exc


@dataclass(frozen=True, order=True)
class Position:
    """A row/column position on the level grid."""

    row: int
    col: int

    def move(self, direction: Direction, steps: int = 1) -> Position:
        row_delta, col_delta = direction.delta
        return Position(self.row + row_delta * steps, self.col + col_delta * steps)


@dataclass
class Tank:
    """A player or enemy tank."""

    tank_id: str
    side: str
    position: Position
    direction: Direction
    hp: int = 1

    @property
    def alive(self) -> bool:
        return self.hp > 0


@dataclass(frozen=True)
class StepReport:
    """Human-readable summary for a completed turn."""

    turn: int
    action: Action
    events: tuple[str, ...]
    result: GameResult


PLAYER_CHARS = {
    "P": Direction.UP,
    "^": Direction.UP,
    "V": Direction.DOWN,
    "v": Direction.DOWN,
    "<": Direction.LEFT,
    ">": Direction.RIGHT,
}
EMPTY_CHARS = {".", " "}
MOVE_ACTION_DIRECTIONS = {
    Action.MOVE_UP: Direction.UP,
    Action.MOVE_DOWN: Direction.DOWN,
    Action.MOVE_LEFT: Direction.LEFT,
    Action.MOVE_RIGHT: Direction.RIGHT,
}
DEFAULT_LEVEL = """
###########
#P..#....E#
#...#.....#
#.........#
#.....#...#
#..E..#...#
###########
"""


class Game:
    """Deterministic turn-based Tank Battle game state.

    Level characters:

    - ``#``: wall
    - ``.`` or space: empty floor
    - ``P`` / ``^`` / ``v`` / ``<`` / ``>``: player tank and initial direction
    - ``E``: enemy tank
    """

    def __init__(
        self,
        *,
        width: int,
        height: int,
        walls: set[Position],
        player: Tank,
        enemies: dict[str, Tank],
        turn: int = 0,
        result: GameResult = GameResult.ONGOING,
    ) -> None:
        self.width = width
        self.height = height
        self.walls = set(walls)
        self.player = player
        self.enemies = dict(enemies)
        self.turn = turn
        self.result = result
        self._validate()
        self._refresh_result()

    @classmethod
    def default(cls) -> Game:
        """Create the built-in playable level."""

        return cls.from_ascii(DEFAULT_LEVEL)

    @classmethod
    def from_ascii(cls, level_text: str, *, player_hp: int = 3, enemy_hp: int = 1) -> Game:
        """Build a game from a rectangular ASCII level."""

        lines = dedent(level_text).strip("\n").splitlines()
        if not lines:
            raise ValueError("level must contain at least one row")

        width = len(lines[0])
        if width == 0:
            raise ValueError("level rows must not be empty")
        if any(len(line) != width for line in lines):
            raise ValueError("level must be rectangular")

        walls: set[Position] = set()
        player: Tank | None = None
        enemies: dict[str, Tank] = {}

        for row, line in enumerate(lines):
            for col, char in enumerate(line):
                position = Position(row, col)
                if char == "#":
                    walls.add(position)
                elif char in EMPTY_CHARS:
                    continue
                elif char in PLAYER_CHARS:
                    if player is not None:
                        raise ValueError("level must contain exactly one player tank")
                    player = Tank(
                        tank_id="player",
                        side="player",
                        position=position,
                        direction=PLAYER_CHARS[char],
                        hp=player_hp,
                    )
                elif char == "E":
                    enemy_id = f"enemy-{len(enemies) + 1}"
                    enemies[enemy_id] = Tank(
                        tank_id=enemy_id,
                        side="enemy",
                        position=position,
                        direction=Direction.DOWN,
                        hp=enemy_hp,
                    )
                else:
                    raise ValueError(f"unsupported level character {char!r} at {row}:{col}")

        if player is None:
            raise ValueError("level must contain exactly one player tank")

        return cls(width=width, height=len(lines), walls=walls, player=player, enemies=enemies)

    @property
    def live_enemies(self) -> tuple[Tank, ...]:
        """Current live enemies sorted by id for deterministic behavior."""

        return tuple(self.enemies[enemy_id] for enemy_id in sorted(self.enemies))

    def in_bounds(self, position: Position) -> bool:
        return 0 <= position.row < self.height and 0 <= position.col < self.width

    def render(self) -> str:
        """Render the current board as ASCII text."""

        grid = [["." for _ in range(self.width)] for _ in range(self.height)]
        for wall in self.walls:
            grid[wall.row][wall.col] = "#"
        for enemy in self.live_enemies:
            grid[enemy.position.row][enemy.position.col] = "E"
        if self.player.alive:
            grid[self.player.position.row][self.player.position.col] = self.player.direction.glyph
        else:
            grid[self.player.position.row][self.player.position.col] = "X"
        return "\n".join("".join(row) for row in grid)

    def step(self, action: Action | str = Action.WAIT) -> StepReport:
        """Advance the game by one player turn plus deterministic enemy responses."""

        resolved_action = Action.from_value(action)
        events: list[str] = []

        if self.result is not GameResult.ONGOING:
            return StepReport(
                turn=self.turn,
                action=resolved_action,
                events=(f"game already finished: {self.result.value}",),
                result=self.result,
            )

        if resolved_action is Action.QUIT:
            self.result = GameResult.QUIT
            return StepReport(
                turn=self.turn,
                action=resolved_action,
                events=("player quit",),
                result=self.result,
            )

        self.turn += 1
        self._apply_player_action(resolved_action, events)
        self._refresh_result()

        if self.result is GameResult.ONGOING:
            self._apply_enemy_ai(events)
            self._refresh_result()

        if not events:
            events.append("nothing happened")

        return StepReport(
            turn=self.turn,
            action=resolved_action,
            events=tuple(events),
            result=self.result,
        )

    def _validate(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("level dimensions must be positive")
        for wall in self.walls:
            if not self.in_bounds(wall):
                raise ValueError(f"wall outside board: {wall}")
        tanks = [self.player, *self.enemies.values()]
        seen_positions: set[Position] = set()
        seen_ids: set[str] = set()
        for tank in tanks:
            if tank.tank_id in seen_ids:
                raise ValueError(f"duplicate tank id: {tank.tank_id}")
            seen_ids.add(tank.tank_id)
            if tank.side not in {"player", "enemy"}:
                raise ValueError(f"unsupported tank side: {tank.side}")
            if not self.in_bounds(tank.position):
                raise ValueError(f"tank outside board: {tank.tank_id}")
            if tank.position in self.walls:
                raise ValueError(f"tank inside wall: {tank.tank_id}")
            if tank.position in seen_positions:
                raise ValueError(f"multiple tanks at {tank.position}")
            seen_positions.add(tank.position)
            if tank.hp < 1:
                raise ValueError(f"tank hp must be positive: {tank.tank_id}")

    def _apply_player_action(self, action: Action, events: list[str]) -> None:
        if action is Action.WAIT:
            events.append("player waits")
            return
        if action is Action.FIRE:
            self._fire(self.player, events)
            return

        direction = MOVE_ACTION_DIRECTIONS[action]
        self._move_tank(self.player, direction, events)

    def _apply_enemy_ai(self, events: list[str]) -> None:
        for enemy_id in sorted(list(self.enemies)):
            enemy = self.enemies.get(enemy_id)
            if enemy is None or not enemy.alive or self.result is not GameResult.ONGOING:
                continue

            shot_direction = self._line_of_sight_direction(enemy.position, self.player.position)
            if shot_direction is not None:
                enemy.direction = shot_direction
                self._fire(enemy, events)
                self._refresh_result()
                continue

            for direction in self._enemy_move_choices(enemy):
                if self._can_move(enemy, direction):
                    self._move_tank(enemy, direction, events)
                    break
            else:
                facing = self._direction_towards(enemy.position, self.player.position)
                if facing is not None:
                    enemy.direction = facing
                events.append(f"{enemy.tank_id} waits")

    def _move_tank(self, tank: Tank, direction: Direction, events: list[str]) -> bool:
        tank.direction = direction
        destination = tank.position.move(direction)
        if not self.in_bounds(destination):
            events.append(f"{tank.tank_id} blocked by edge")
            return False
        if destination in self.walls:
            events.append(f"{tank.tank_id} blocked by wall")
            return False
        occupant = self._tank_at(destination)
        if occupant is not None and occupant.tank_id != tank.tank_id:
            events.append(f"{tank.tank_id} blocked by {occupant.tank_id}")
            return False

        tank.position = destination
        events.append(f"{tank.tank_id} moved {direction.value}")
        return True

    def _can_move(self, tank: Tank, direction: Direction) -> bool:
        destination = tank.position.move(direction)
        if not self.in_bounds(destination) or destination in self.walls:
            return False
        occupant = self._tank_at(destination)
        return occupant is None or occupant.tank_id == tank.tank_id

    def _fire(self, shooter: Tank, events: list[str]) -> None:
        if not shooter.alive:
            return

        target = self._first_tank_in_direction(shooter)
        if target is None:
            events.append(f"{shooter.tank_id} fired and missed")
            return
        if target.side == shooter.side:
            events.append(f"{shooter.tank_id} shot blocked by {target.tank_id}")
            return

        target.hp -= 1
        events.append(f"{shooter.tank_id} hit {target.tank_id}; hp={max(target.hp, 0)}")
        if target.hp <= 0:
            events.append(f"{target.tank_id} destroyed")
            if target.side == "enemy":
                self.enemies.pop(target.tank_id, None)

    def _first_tank_in_direction(self, shooter: Tank) -> Tank | None:
        position = shooter.position.move(shooter.direction)
        while self.in_bounds(position):
            if position in self.walls:
                return None
            target = self._tank_at(position)
            if target is not None:
                return target
            position = position.move(shooter.direction)
        return None

    def _tank_at(self, position: Position) -> Tank | None:
        if self.player.alive and self.player.position == position:
            return self.player
        for enemy in self.enemies.values():
            if enemy.alive and enemy.position == position:
                return enemy
        return None

    def _refresh_result(self) -> None:
        if self.result is not GameResult.ONGOING:
            return
        if not self.player.alive:
            self.result = GameResult.LOSE
        elif not self.enemies:
            self.result = GameResult.WIN

    def _enemy_move_choices(self, enemy: Tank) -> tuple[Direction, ...]:
        row_delta = self.player.position.row - enemy.position.row
        col_delta = self.player.position.col - enemy.position.col
        vertical = Direction.DOWN if row_delta > 0 else Direction.UP if row_delta < 0 else None
        horizontal = Direction.RIGHT if col_delta > 0 else Direction.LEFT if col_delta < 0 else None

        preferred: list[Direction] = []
        if abs(col_delta) > abs(row_delta):
            preferred.extend(direction for direction in (horizontal, vertical) if direction is not None)
        else:
            preferred.extend(direction for direction in (vertical, horizontal) if direction is not None)

        directions = tuple(Direction)
        order = {direction: index for index, direction in enumerate(directions)}
        fallback = sorted(
            (direction for direction in directions if direction not in preferred),
            key=lambda direction: (
                self._distance(enemy.position.move(direction), self.player.position),
                order[direction],
            ),
        )
        return tuple([*preferred, *fallback])

    def _line_of_sight_direction(self, start: Position, end: Position) -> Direction | None:
        direction = self._direction_towards(start, end)
        if direction is None:
            return None

        position = start.move(direction)
        while self.in_bounds(position):
            if position in self.walls:
                return None
            tank = self._tank_at(position)
            if tank is not None:
                return direction if tank.position == end else None
            position = position.move(direction)
        return None

    @staticmethod
    def _direction_towards(start: Position, end: Position) -> Direction | None:
        if start.row == end.row:
            if end.col > start.col:
                return Direction.RIGHT
            if end.col < start.col:
                return Direction.LEFT
        if start.col == end.col:
            if end.row > start.row:
                return Direction.DOWN
            if end.row < start.row:
                return Direction.UP
        return None

    @staticmethod
    def _distance(first: Position, second: Position) -> int:
        return abs(first.row - second.row) + abs(first.col - second.col)

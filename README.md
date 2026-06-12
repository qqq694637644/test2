# Tank Battle

这是一个无第三方运行时依赖的 Python 终端版坦克大战项目。

游戏采用确定性的回合制规则：玩家输入一次动作后，所有敌方坦克按固定 AI 行动一次。核心逻辑与终端输入输出分离，便于单元测试覆盖地图解析、移动、射击、敌方 AI、渲染和胜负判定。

## 功能

- ASCII 地图：`#` 是墙，`.` 或空格是地面，`P/^/v/</>` 是玩家坦克，`E` 是敌方坦克。
- 玩家可移动、转向、开火、等待或退出。
- 子弹沿当前方向直线飞行，遇墙停止，命中敌方会造成伤害。
- 敌方 AI 会优先在同一直线开火，否则按确定性规则靠近玩家。
- 支持交互运行，也支持 `--actions` 脚本动作，方便 smoke test 和 CI 验证。

## 运行

源码目录运行：

```text
PYTHONPATH=src python -m tank_battle
```

Windows PowerShell 源码目录运行：

```text
$env:PYTHONPATH = "src"; python -m tank_battle
```

安装后运行：

```text
python -m tank_battle
```

或使用 console script：

```text
tank-battle
```

## 操作

```text
W/A/S/D 或 up/down/left/right  移动并转向
F 或 fire                      开火
Enter 或 wait                  跳过回合
Q 或 quit                      退出
```

## 脚本动作

```text
PYTHONPATH=src python -m tank_battle --actions fire,wait --max-turns 5
```

自定义地图文件：

```text
PYTHONPATH=src python -m tank_battle --level-file path/to/level.txt
```

## 本地验证

```text
python -m compileall -q src tests
python -m pytest -q
python -m ruff check src tests
PYTHONPATH=src python -m tank_battle --actions fire,wait --max-turns 5
```

开发依赖在 `pyproject.toml` 的 `dev` extra 中声明：

```text
python -m pip install -e .[dev]
```

## 项目边界

本仓库只承载 `tank_battle` 游戏包、命令行入口和相关测试。

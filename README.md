# VM Test Template

这是一个可复用的 HostMachine / disposable guest VM / guest agent / host controller 实机测试模板。

它适合给新项目快速搭建以下测试链路：

```text
GitHub workflow_dispatch
  -> self-hosted Windows runner on HostMachine
  -> optional VM restore/start
  -> guest agent connects back to host controller
  -> host serves one-shot payload
  -> guest runs payload
  -> host collects logs/result/artifact
```

模板默认只提供通用动作：

- `noop`
- `command`
- `python_script`

具体项目应该在自己的仓库里新增项目专属 payload builder 和断言，不要把项目专属样本、应用路径或历史失败流水账写回模板仓库。

## 快速使用

1. 在 GitHub 上点击 **Use this template** 创建新仓库。
2. 按项目需要修改 `.github/workflows/disposable-vm-guest-agent-smoke.yml` 的默认输入。
3. 在 guest VM 快照里安装 agent 依赖：

```text
py -3.11 -m pip install -r src/vm_test_template/guest_vm/requirements.txt --proxy http://192.168.1.249:10810
```

4. 启动 guest agent，例如：

```text
py -3.11 -m vm_test_template.guest_vm.agent --controller-url http://192.168.1.249:8766
```

5. 从 GitHub Actions 手动触发 `Disposable VM guest agent smoke` workflow。

## 维护规则

详细规则见：

```text
TESTING_HANDOFF.md
```

通用经验见：

```text
PORTABLE_WORKFLOW_DEVELOPMENT_LESSONS.md
```

硬规则摘要：

- workflow 只用 `shell: python` 调用仓库里的 Python 文件。
- 不用长 PowerShell/cmd 写测试逻辑。
- Python 拉取任何三方库都显式带：`--proxy http://192.168.1.249:10810`。
- 项目专属经验不要写进模板仓库。

## 本地验证

```text
python -m pytest -q
python -m compileall -q src tests
```

## 命令行贪吃蛇

安装本项目后可以在终端里运行一个纯标准库实现的贪吃蛇小游戏：

```text
python -m pip install -e .
snake-game
```

也可以直接用模块方式运行：

```text
python -m vm_test_template.snake --width 30 --height 15 --speed 8
```

操作方式：`WASD` 或方向键移动，`P` 暂停，`Q` 退出。

# VM 测试模板接手规则

Last updated: 2026-06-05

这个文件的用途类似 `AGENTS.md`：给后续维护者和 AI 助手说明本模板仓库的工作规则。这里不要写某个具体业务项目的断言、样本文件、产品接口或历史失败细节；那些内容应该留在使用本模板创建出来的项目里。

## 1. 模板定位

这套仓库是可复用的 HostMachine / disposable guest VM / guest agent / host controller 测试骨架。适合需要真实机器、虚拟机、桌面应用、GUI 程序、长时间集成目标或跨进程端到端测试的项目。

新项目可以从这个模板开始，只替换项目自己的：

- payload builder
- 测试断言
- 样本路径或应用路径
- workflow inputs
- README 中的项目说明

通用经验放在 `PORTABLE_WORKFLOW_DEVELOPMENT_LESSONS.md`。不要把 `DISPOSABLE_VM_WORKFLOW_LESSONS.md` 这类项目运行流水账放进模板仓库；它会和 portable lessons 重复，并污染模板。

## 2. 维护硬规则

- 永远优先用 Python 文件开发和承载逻辑，不要把主要逻辑写进 PowerShell、cmd、workflow `run:` 多行命令或临时拼接命令里。
- workflow 只能做 checkout、用 `shell: python` 调用 Python 文件、上传 artifact 这类胶水动作。
- 复杂分支、参数解析、payload 生成、controller 参数组装必须放进仓库里的 `.py` 文件。
- 一个测试入口对应一个可 review 的 Python 脚本或 payload builder。
- workflow inputs 由 Python 从 GitHub 自动提供的 `GITHUB_EVENT_PATH` 读取；不要在 YAML 里维护大段手工 env 映射。
- 不要使用 `shell: cmd`、`shell: pwsh` 承载测试逻辑。
- 新增或修改 workflow 行为时，必须给 Python 文件加本地单测、`py_compile` 或至少 `compileall`。

## 3. Python 三方库代理规则

Python 拉取任何三方库时必须显式加代理参数：

```text
--proxy http://192.168.1.249:10810
```

标准例子：

```text
py -3.11 -m pip install -r requirements.txt --proxy http://192.168.1.249:10810
```

不要写裸 `pip install`，不要依赖环境变量隐式代理。模板中的 host/controller 自动安装和 workflow 安装都必须遵守这条规则。

## 4. 通用链路

```text
GitHub workflow_dispatch
  -> HostMachine self-hosted Windows runner
      -> checkout repository
      -> shell: python -> runpy.run_path("src/vm_test_template/disposable_vm/workflow_install.py")
      -> shell: python -> runpy.run_path("src/vm_test_template/disposable_vm/workflow_runner.py")
      -> workflow_runner.py reads GITHUB_EVENT_PATH inputs
      -> host_controller starts on 0.0.0.0:<port>
      -> optional: restore/start guest VM
      -> wait guest agent POST /hello
      -> serve dynamic payload at /payload/{job_id}
      -> receive guest /log/{job_id}
      -> receive guest /result/{job_id}
      -> upload artifact
```

Guest VM 侧：

```text
guest snapshot boots
  -> guest agent starts
  -> POST <controller_url>/hello
  -> GET  <controller_url>/payload/{job_id}
  -> run payload: noop / command / python_script
  -> POST <controller_url>/log/{job_id}
  -> POST <controller_url>/result/{job_id}
```

## 5. 文件地图

| 文件 | 用途 |
| --- | --- |
| `.github/workflows/disposable-vm-guest-agent-smoke.yml` | 最薄的 GitHub Actions 入口，只调用 Python 文件。 |
| `src/vm_test_template/disposable_vm/workflow_install.py` | workflow 内安装当前项目，所有 pip 安装显式走代理。 |
| `src/vm_test_template/disposable_vm/workflow_runner.py` | 读取 workflow inputs、生成 payload、启动 host controller。 |
| `src/vm_test_template/disposable_vm/host_controller.py` | HostMachine 侧一次性控制器，提供 `/hello`、`/payload`、`/log`、`/result`。 |
| `src/vm_test_template/guest_vm/agent.py` | Guest VM 侧 agent，主动连接 host controller 并执行 payload。 |
| `src/vm_test_template/payload/disposable_vm.py` | host/guest 共用 JSON 协议模型。 |
| `PORTABLE_WORKFLOW_DEVELOPMENT_LESSONS.md` | 可迁移的成功/失败经验总结。 |

## 6. 新项目从模板开始时

1. 用 GitHub `Use this template` 创建项目。
2. 保留 `disposable_vm`、`guest_vm`、`payload` 基础骨架。
3. 在新项目里新增项目专属 payload builder，例如 `src/<package>/payload/<project>_smoke.py`。
4. 只在新项目中记录项目专属样本路径、应用路径、断言和 run ID。
5. 首次验证先跑 `noop` 或 `command`，确认 HostMachine/guest 通路。
6. 再跑项目专属 payload。
7. 每次实机 run 都保存 run ID、artifact ID、关键 controller/result 字段。

## 7. 基线验证命令

本地：

```text
python -m pytest -q
python -m compileall -q src tests
```

Guest VM 依赖检查：

```text
py -3.11 -m vm_test_template.guest_vm.required_imports
```

Guest VM 依赖安装：

```text
py -3.11 -m pip install -r src/vm_test_template/guest_vm/requirements.txt --proxy http://192.168.1.249:10810
```

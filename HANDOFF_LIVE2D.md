# Live2D shared-state-machine 交接文档

> 本文件是 Live2D 交接资料。已按授权提交到个人工作分支；不要创建或更新 PR。

## 当前工作状态

- 仓库：`Iris0fTheValley/D_sakiko`
- 当前分支：`feat/live2d-shared-state-machine`
- 当前 HEAD：`d1623e7 fix: handle model switch before renderer hello`
- donor 分支：`feat/electron-live2d-statemachine`，只作为 Electron/UI 代码来源，不要继续在其上开发。
- 工作区在创建本文档前是干净的；本文档本身应保持为本地未跟踪文件。
- 真实环境：`J:\AI friend\sairi\DSakiko3.10`
- 源码环境：`J:\AI friend\sairi\D_sakiko`

## 总体架构约束

Python `Live2DBehaviorController` 是唯一的 Live2D 业务状态机，位于：

`GPT_SoVITS/live2d_controller.py`

它唯一决定：

- idle / idle recover / timed idle
- thinking
- emotion
- click / click throttle
- long audio repeat
- bye
- maskoff / 祥子黑白切换
- motion group、index 和 motion 生命周期
- 业务 expression
- renderer readiness 与模型切换完成

Electron 的 `Live2DRendererController` 位于：

`electron_frontend/src/renderer/renderer-controller/Live2DRendererController.ts`

它只是 renderer command executor，负责 SDK、模型、音频 owner、lip sync、参数写入、动作完成回报和窗口本地状态。不要把 idle、random、thinking、emotion scheduling 或 expression 规则重新放回 Electron。

数据流：

```text
Python Live2DBehaviorController
        │  load_model / play_motion / set_expression / play_audio
        ▼
Bridge + WebSocket
        ▼
Electron renderer A / renderer B
        │  renderer_ready / motion_finished / audio_ended / renderer_disconnected
        └──────────────► Python controller
```

## Expression 规则

统一使用：

`GPT_SoVITS/live2d_support/expression_policy.py::select_expression_for_motion()`

Python 从当前模型 JSON 读取 `group/index → motion_file`，使用同一 policy 解析 expression。Electron 只上报实际支持的 `expression_ids` 并执行 `set_expression`。

相关代码：

- `GPT_SoVITS/live2d_support/expression_policy.py`
- `GPT_SoVITS/live2d_support/motion_capabilities.py`
- `GPT_SoVITS/live2d_support/runtime_adapter.py`
- `GPT_SoVITS/live2d_controller.py`

不要重新添加 `expression_candidates_for_emotion()` 或 emotion→expression 的第二套映射。

## 冷启动竞态修复

关键时序现在允许初始模型切换早于 renderer 连接：

1. 启动队列可能先触发 `switch_model()`，此时 `model_expected=[]`，controller 进入 `switching`。
2. Electron 发送 `renderer_hello`。
3. controller 发现该 renderer 尚未在当前切换目标中，将其加入 `model_expected`，并向它重发带当前 token 的 `load_model`。
4. bootstrap 模型发出的空 token `renderer_ready` 不会完成切换。
5. renderer 加载 Python 下发的权威模型后发送正确 token 的 `renderer_ready`。
6. controller 完成模型切换；`main2.py` 只有在 controller 接受该 ready 且离开 `switching` 后才设置 `electron_renderer_ready`。
7. 首条消息随后才允许进入 emotion/motion/expression/audio 流程。

重要位置：

- `GPT_SoVITS/live2d_controller.py::_on_renderer_ready_locked()`
- `GPT_SoVITS/main2.py::_switch_electron_model()`
- `GPT_SoVITS/main2.py::_renderer_ready_completed_model_switch()`
- `GPT_SoVITS/main2.py::handle_electron_model_response_payload()`

不要把“收到 `renderer_ready` 包”直接当成业务 ready。bootstrap ready、错误 token、重复/过期 ready 都不能释放首条消息 gate。

## 已完成的关键修复提交

从旧到新：

- `2468b76`：统一 expression 生命周期
- `ecbfbff`：恢复 shared Live2D renderer 生命周期
- `ceeae98`：保留祥子 variant
- `6cb67e8`：同步翻译文本与音频片段显示
- `0b5c823`：恢复 Electron bootstrap renderer
- `ba3b998`：使用 file URL 安全的 bootstrap model
- `2be0d6f`：复用共享 motion expression policy
- `b8852c9`：ready gate 只接受权威模型完成
- `d1623e7`：处理 renderer hello 早于首个 model switch 的顺序

## 真实环境同步

修改源码后，先确保真实环境中的 Python/Electron 进程已结束，再执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\sync_real_environment.ps1
```

脚本会同步：

- `GPT_SoVITS`
- `bridge`
- `electron_frontend/src` 等非 `node_modules/dist/out` 内容
- `run.bat`、启动配置和 launcher

如果修改了 Electron TypeScript/Vue，真实环境使用的是 `electron_frontend/dist`，需要额外构建：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\sync_real_environment.ps1 -BuildElectron
```

目标 runtime 当前是 Python 3.9；同步脚本会提示上游正式要求 Python 3.11，但现有代码包含兼容路径。

不要先用旧目标文件覆盖源码，也不要把真实环境的临时配置、用户数据或模型反向复制回仓库。

## 已验证命令

源码 Live2D 测试：

```powershell
python -m unittest GPT_SoVITS.test.test_live2d_controller GPT_SoVITS.test.test_live2d_motion_semantics GPT_SoVITS.test.test_live2d_runtime_policies -q
```

最近结果：35 tests，`OK`。

Python 编译检查：

```powershell
python -m compileall -q GPT_SoVITS/main2.py GPT_SoVITS/live2d_controller.py
```

Electron 检查：

```powershell
cd electron_frontend
npm run typecheck
npm run test:cold-start
```

最近结果：typecheck 通过，`Electron cold-start bootstrap checks passed.`

最近一次真实环境启动复现已观察到完整握手：

```text
初始 switch_model targets=[]
renderer_hello
重发定向 load_model
bootstrap renderer_ready token= -> rejected
authoritative renderer_ready token=<current model token> -> accepted
ready gate released
```

## 后续接手者优先验证

1. 关闭所有程序后直接启动 `J:\AI friend\sairi\DSakiko3.10\run.bat`。
2. 不切换对话，直接发送第一条消息，确认 thinking、emotion motion、expression、audio、lip sync 和字幕均正常。
3. 测试单窗口、双窗口、关闭/重连 renderer。
4. 测试黑祥/白祥切换后直接对话，以及切换对话后 variant 不丢失。
5. 测试模型切换、连续多段回复和 audio + lip sync。
6. 测试 Electron resize、边缘 hover、四角圆弧和主题色过渡。

## 修改原则

- 不创建 PR，不主动影响 upstream。
- 只推送到自己的远端分支 `feat/live2d-shared-state-machine`。
- 简单修改由 Luna 完成；跨 Python/Electron/Bridge 的中等问题交 Terra；状态机架构、并发、生命周期和难复现问题交 Sol。
- 每次修改后先测试源码，再同步真实环境；手动测试前结束相关进程。
- 禁止 queue monkeypatch、隐式 side-channel/偷听业务 Queue，或隐藏 Pygame renderer 作为新方案；Electron 模式允许 compatibility adapter 作为显式、唯一且受 contract 约束的 legacy queue/flag consumer，但不得在其中加入业务决策。
- 不在 Electron 增加第二套业务状态机。

## Electron Live2D compatibility audit（审查基线）

本节只记录审查门禁结果与下一轮批准任务；不表示下列行为已经修复、已经等价或已经通过运行时验证。

### Baseline

- `master` HEAD：`7ef77f317a5f9496ad133d52e039f2e3a4bf2377`
- 工作分支 `feat/live2d-shared-state-machine` HEAD：`d1623e7fff510566c41b766ffc894c737b30beb4`
- `HANDOFF_LIVE2D.md`：可读取；当前为未跟踪本地文件。
- 后续审查结论必须绑定以上两条 SHA；任一 GitHub branch/commit 变化后必须重新建立 baseline。

### Gate verdict

- Gate A — Upstream contract inventory：PASS（静态 E1/E2）。已从 master 的 queue、flag、command dispatch、Qt 入口及 Live2D runtime 盘点原子行为契约。
- Gate B — Current implementation mapping：PASS（静态 E1/E2）。已逐项核对 upstream source → current Electron path → controller → renderer；PASS 仅表示 mapping 审查完成，不表示功能保留。
- Gate C — Compatibility architecture：PASS（静态 E1/E2）。批准 thin adapter 边界；PASS 仅表示架构门禁通过，不表示实现完成或行为等价。

### Evidence scope and current confirmed gaps

- E0 聊天陈述未用于通过 Gate。
- E1：文件、调用点、command/queue、controller/renderer 源码位置。
- E2：跨文件调用链、共享变量消费者和 controller/renderer 边界互相核对。
- 本轮没有新的 E3；真实播放、断线竞态、多 renderer 聚合及冷启动后的完整业务链仍需运行时复核。
- 当前确定缺口/回归面包括：thinking start/stop、talking、text/visibility、history replay、audio-only `motion_complete_value` compatibility projection、background、FPS、layout，以及 viewer/theater 行为；另有 motion failure、model fallback、V2/V3 capability 差异需继续核对。
- `renderer_disconnect` 对 busy 的处理仍为 Unknown，除非 controller 明确给出 matching cancellation/ended fact。

### Approved next minimal implementation task（未实施）

只允许生成并执行一个最小任务包：

1. 建立 compatibility adapter，将既有 legacy events/values 纯转发为 controller semantic intent 或 renderer fact；adapter 不得包含 timer、random、priority、emotion mapping 或第二套业务状态机。
2. 增加 audio-busy reverse compatibility API：仅由 matching `audio_started`、`audio_ended`、`audio_failed` 及 controller 已确认的 matching `stop_audio`（包括 `model_switch`、`reset`、`cancel`）投影旧 `motion_complete_value`；stale/foreign stop 忽略，switch 本身不是输入，disconnect 保持 Unknown。

该任务不包括 viewer/theater、V3、background/FPS/layout 的实现，不包括新状态机，不包括删除或重写上游业务逻辑。

### Git status at handoff update

- 业务代码、测试、配置未修改。
- 本次交接更新已获授权提交并 push 到个人工作分支；不得创建或更新 PR。

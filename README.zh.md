# patch-witness

**这条回归测试在修复前真的会失败吗？** 把同一份 head 测试分别放到 base/head 源码快照上，输出逐测试 JSON 证据。

[English](README.md) · [真实案例](examples/README.md)

用于多 Agent 代码审查：CI 证明当前测试通过；patch-witness 进一步记录具体测试是否识别了修复前的差异。零运行时依赖，不调用模型。

## 使用

需要 Python 3.11+、Git、Linux/macOS。直接在克隆目录运行：

```sh
git clone https://github.com/original4422/patch-witness.git
cd patch-witness
python3 -m patch_witness \
  --repo ../repo-workbench \
  --base 3660841^ --head 3660841 \
  --test-file tests/test_workbench.py \
  --test-id GitHubTests.test_paginated_gh_contract_requests_exact_sha
```

目标仓库和提交须已存在本地。`--test-id` 可重复；`src/` 布局使用 `--source-root src`；`--timeout 30` 设置每个测试每侧的超时。也可 `python3 -m pip install .` 安装 `patch-witness` 命令。

报告包含完整 commit SHA、head 测试文件 SHA-256、Python 版本、收集/执行数量、两侧事件和状态、快照内导入路径。退出码：`0` 所选测试全部构成 witness；`1` 至少一条不构成；`2` 参数或快照准备失败。

## 使用已有依赖环境

带第三方依赖的项目可同时传入三个参数：

```sh
python3 -m patch_witness \
  --repo ../agent-serving-lab \
  --base c4c03b63ae56e0aef1aa00cc6b587cd27cd6bf8b \
  --head 93460c5afd055b19c10781210c4c5429ba895f88 \
  --test-file tests/test_deadlines.py \
  --test-id DeadlineTests.test_deadline_crossed_during_selection_never_enters_backend \
  --python ../agent-serving-lab/.venv/bin/python \
  --dependency-dir ../agent-serving-lab/.venv/lib/python3.13/site-packages \
  --package serving_lab
```

解释器和依赖目录使用现成环境的实际路径。`--dependency-dir` 与 `--package` 可重复；包范围是带 `__init__.py` 的顶层常规 Python 包，`src/` 布局再加 `--source-root src`。base/head 使用同一解释器和目录。

worker 保持 **`-I -S -B`**，直接追加依赖目录，不处理 `.pth`、`sitecustomize` 或 editable hook。声明包及其子模块只从各自 Git 快照解析；缺失或来源越界记为 `source_error`，不会从环境中同名安装包补齐。

逐侧记录 worker Python 版本/实现/解释器 hash、依赖 distribution 名称/版本/metadata hash、已导入被测模块的快照路径及 hash；顶层 `python` 仍表示 launcher。两侧观测身份不一致时 `comparison_status: environment_mismatch`，身份缺失时为 `environment_unavailable`，均不判 witness。这是元数据观测，不是锁文件校验或完整环境重建。工具不安装、构建或修改环境。

[serving 真实案例](examples/serving-deadlines.md) 使用带 editable 安装的现成环境读取 httpx，两个指定测试 F2P、一个取消测试 P2P。

## 判定

仅 **测试正文中的 AssertionError → 通过** 算作 witness。每条测试、每个版本均使用新快照；通过要求恰好收集并执行一条测试、结果完整、进程正常退出。

- 旧版也通过：P2P 对照，`witness: false`。
- skip、预期失败、意外成功：单列，不算 witness。
- 导入/收集、setup/teardown/cleanup、运行时错误：分别记录，不能充当正文断言失败。
- 零测试、多测试、超时、进程退出、结果缺失/无效：分别记录。单凭退出码 0 不判成功。

父进程检查结果并计算 witness；worker 用 unittest 回调与阶段 hook 记录事件。使用 `-I -S` 排除继承的 `PYTHONPATH` 和 site-packages，只加入快照、测试目录及显式源码目录。测试自行启动的子进程需自行保证导入来源；工具在 worker 结束或超时后清理它的进程组。

## 执行范围

通过 Git `ls-tree` / `cat-file` 读取提交到临时目录，不 checkout/reset/fetch，也不写目标仓库。两侧只覆盖指定的 head 测试文件，其余 helper/fixture 保留各自版本。首版支持常规文件，拒绝符号链接和 submodule；默认模式依赖标准库与仓库源码，显式模式读取已有依赖目录。

测试和源码是受信任代码。临时快照、独立判定进程不构成恶意 Python 的安全沙箱或防篡改边界。witness 记录具体测试的行为变化，审查时仍需确认断言对应预期修复。

## 实际验证

预先选定两例，测试均未改写：

| 修复 | base | head |
|---|---|---|
| repo-workbench `3660841` 补 `filter=all` | 请求缺少参数，断言失败 | 通过 |
| learn-codex `9327755` 加强标签验收 | 错误候选被旧验收器放行，断言失败 | 新验收器拒绝错误候选，通过 |

另选两条已有行为测试，均 P2P 且不被算作 witness。两个目标仓库的 HEAD、index 原始字节和 tracked/未忽略 untracked 文件内容前后完全一致。[查看证据与复现命令](examples/README.md)。本地为 macOS / CPython 3.14.7，CI 覆盖 Linux/macOS。

```sh
python3 -m unittest discover -s tests -v
```

39 项测试覆盖真实 Git 快照、脏工作区、导入来源、关键反例及超时子进程清理。[相关工具及定位](README.md#related-tools)。MIT。

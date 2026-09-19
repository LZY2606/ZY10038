# 测试与验证指南

本文档说明如何在本地运行边界矩阵、重放 fuzz seed、理解子进程保护机制，
以及如何执行自动化变异验证。所有命令都从仓库根目录直接运行，不需要
外部服务、手工环境变量或公网访问。

## 准备

```bash
python3 -m pip install -e '.[testing]'
```

## 本地运行

```bash
python3 -m pytest -q
```

退出码为零即通过。输出末尾的 `boundary matrix / fuzz / mutation stages`
小节会逐条列出新增边界矩阵、fuzz 与变异用例的名称及结果。

## 边界矩阵（parsimonious/tests/test_boundary_matrix.py）

36 个命名测试，按组合维度分组：

- `TestNullableQuantifiers`：可空表达式进入 `*`、`+` 与嵌套量词。
- `TestLookaheadZeroWidth`：正/负 lookahead 与零宽 regex 组合。
- `TestTokenGrammarBoundary`：TokenGrammar 在空 token 流、最后一个
  token 与 EOF 上的 match/parse 差异（包括当前 EOF 处抛 `IndexError`
  的既有行为，已显式固定）。
- `TestForwardRefsAndLeftRecursion`：前向引用、直接与间接左递归检测。
- `TestSharedPrefixes`：左右分支共享前缀时的有序选择与最远错误位置。
- `TestUnicodeIndexes`：Unicode code point 与 Python 字符索引的一致性。
- `TestNodeVisitorOrder`：NodeVisitor 在空节点、嵌套节点与访问方法
  抛错时的遍历顺序。

失败场景断言最远位置（`error.pos`）、规则名（`error.expr.name`）与稳定
的上下文片段（`error.text[pos:pos+5]`）；成功场景断言消费长度与节点
跨度（`node.start`/`node.end`）。

## 子进程保护（parsimonious/tests/subprocess_guard.py）

可能不前进的重复（如 `(~"b*")*`）在独立子进程中运行：
`match_in_subprocess()` 用 `subprocess.run(..., timeout=1.0)` 让**父进程
在一秒内判定结果**。子进程超时判为 hang（`SubprocessHang`），异常退出
判为 crash（`SubprocessCrash`），诊断信息包含 grammar 与输入 code
points。不依赖任何测试套件级全局 timeout。

## Fuzz 与 seed 重放（parsimonious/tests/grammar_fuzz.py）

`test_grammar_fuzz.py` 使用固定 seed（默认 `1042`）生成深度 ≤ 4、节点数
≤ 12 的语义安全 grammar（`*`/`+` 只包裹非可空表达式，引用只指向更早
的规则，因此不会死循环也不会左递归）。每个样本验证：

- `parse` 与 `match` 的消费关系（match 消费全部 ⇔ parse 成功）；
- 添加无害分组、`x` → `(x / x)` 等价单项分支两种变形后消费不变；
- 末尾追加显式 EOF 哨兵 `!~"."` 的变形与 parse 的 EOF 检查一致。

失败输出包含 seed、生成的 grammar、输入 code points 以及贪心删除得到
的最小可复现输入。重放其它 seed：

```bash
PARSIMONIOUS_FUZZ_SEED=<seed> python3 -m pytest -q parsimonious/tests/test_grammar_fuzz.py
```

`test_generation_is_deterministic_across_runs` 保证同一 seed 连续两次
生成的样本顺序与检查结果完全一致。

## 自动化变异（parsimonious/tests/mutation_harness.py）

模拟三类缺陷，每类都必须被边界矩阵中指定的测试抓住：

1. `furthest_error_becomes_last`：最远错误选择被改成最后错误
   （`pos >= error.pos` → `True`）。
2. `zero_width_repetition_continues`：零宽重复允许继续（`Quantifier`
   的零宽守卫被禁用，子进程保护必须在 1 秒内判 hang）。
3. `token_grammar_eof_off_by_one`：TokenGrammar 的 EOF 位置偏移
   （`token_list[pos]` → `token_list[pos + 1]`）。

运行方式（已包含在默认 pytest 运行中，也可单独执行）：

```bash
python3 -m pytest -q parsimonious/tests/test_mutation.py
# 或直接查看人类可读报告（可只跑指定变异）：
python3 -m parsimonious.tests.mutation_harness [mutation_name ...]
```

每个变异按 `apply → run → restore → workspace` 四个阶段执行；变异后的
pytest 进程异常退出（崩溃或超时）会以 `MutationStageError` 报告对应
阶段。无论成败，源文件都会在 `finally` 中恢复，并与变异前的
`git status --porcelain` 基线对比，确认工作区无残留。

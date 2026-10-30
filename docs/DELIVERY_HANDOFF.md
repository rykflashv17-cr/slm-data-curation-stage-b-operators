# V4 四算子版本：结果交付与验证

> 历史记录：当前接口、四项复查修复与最新验证以 [REVIEW_FIX_HANDOFF.md](REVIEW_FIX_HANDOFF.md) 为准。

日期：2026-10-29。本地分支：`codex/v4-result-delivery`。代码基线：`8a2760d454afd4d6db575ea879b73150a0976939`。本次代码包包含此前两个 P0 修复、前三算子严格串行及新增 B04。旧交付包保持原样；本地修改未提交或推送远端。

## 职责与 PM 图的对应

```text
B01 格式统一 → B02 全局统计 → B03 质检判断 → B04 结果交付
                                             ├─ kept：复制完整合格 MCAP 到 accepted/
                                             ├─ rejected：不输出，按模式保留或删除原件
                                             └─ error：不输出、不删除，记录错误
```

新增注册 ID 为 `mcap_delivery`，类为 `stage_b_mcap_v4.delivery.McapDeliveryOperator`。B04 对应 PM 图右侧“输出合格 MCAP”和“删除不合格 episode”两个结果分支。B03 现在只判定和保存证据，不再调用删除。

B04 不重跑 B01/B02/B03，不应用新质量阈值，不修改质量结论。合格输出是独立的原始 MCAP 副本，逐字节复制，不生成硬链接、不截帧、不改 topic/schema、不重新编码视频。保留输入根目录下的相对路径，支持不同任务中同名的 `episode.mcap`。`accepted/` 内只有 MCAP；ABC parquet 等 sidecar 不复制、不删除。

本次未改动 B03 的数值诊断策略：缺测命中时跳过 S1–S4；否则四项仍全部运行后合并。PM 图中“每项检测到不合格就直接终止”的短路行为尚未实现，新增算子四只补齐结果交付。

## 四阶段接口

```python
from stage_b_mcap_v4 import (
    McapReadOperator, GlobalStatsOperator, QualityDetectionOperator,
    McapDeliveryOperator, load_quality_batch,
)

normalized = McapReadOperator().run_batch(refs, spec, cfg)
statistics = GlobalStatsOperator().run(normalized, spec, cfg)
judged = QualityDetectionOperator().run_batch(statistics, spec, cfg)
delivered = McapDeliveryOperator().run(judged, mode="quality")

# 或在另一进程读取已完成 B03、尚未执行 B04 的同一输出目录：
judged = load_quality_batch("/absolute/reports/quality_batch.json")
delivered = McapDeliveryOperator().run(judged, mode="quality")
```

上下两种 B04 调用是替代方式，不能对同一输出目录重复执行。第二种方式不依赖标准 NPZ 或数值引擎对象，但需要原始源路径仍可访问。B02→B03 的统计对象仍使用进程内接口，跨容器部署还需平台对接；不能把新增 B04 视为整个平台部署完成。

B03 返回 `BatchQualityResult(summary, exit_code, manifest_path)`。`quality_batch.json` 引用判定 JSONL 和质量汇总，保存两者 SHA256。B04 重新读取并校验保存的文件，不信任调用方修改的内存汇总；同时检查条目数量、重复源/编号及拒绝标志一致性。

B04 返回 `BatchDeliveryResult(summary, exit_code)`。`kept_files` 表示质量通过数；`exported_files` 才表示实际交付数。`delivery_manifest.json.status=complete` 表示交付阶段完成，若本批另有输入错误，最终退出状态仍为非零。

## 命令与结果

| 命令 | 阶段 | 原件处理 | 独立合格目录 |
|---|---|---|---|
| `stats` | 1→2 | 全部保留 | 不生成 |
| `quality` | 1→2→3→4 | 全部保留 | 生成 `accepted/` |
| `run` | 1→2→3→4 | 保留通过和错误项；删除明确拒绝且身份核对通过的原件 | 先完成 `accepted/`，再执行删除 |

现有 CLI 参数保持；每次使用新的 `--output-root`。旧的 B03 `mode` 参数与 `delete_rejected` 方法已移除，模式只传给 B04。`quality` 现在也会占用合格文件副本的存储空间。

```text
output-root/
  normalized/                  B01 标准中间数据
  cache/                       B02 数值统计缓存
  statistics_context.json      B02 纳入情况
  decisions.jsonl              B03 全批质量判断
  quality_summary.json         B03 汇总
  quality_batch.json           B03→B04 交接文件
  accepted/                    B04 合格 MCAP 副本，保留相对路径
  delivery_plan.json           B04 输出计划
  delivery_manifest.json       B04 完成状态、映射、大小与 SHA256
  deletion_plan.json           B04 源文件处置计划
  deletion_events.jsonl        run 模式的实际处置记录
  run_summary.json             B04 最终汇总
```

先在暂存目录完成全部合格文件复制、身份复核和持久化，再发布 `accepted/`。随后保存交付清单和删除计划，最后才允许源文件删除。复制中途失败不发布半个批次，整批删除禁用。若目录发布后的同步或审计写入失败，已发布目录可能存在，但无成功交付证明，流程会报错且不继续删除。

已有输出不会覆盖或自动续跑。不能对已运行 `quality` 的交付目录再调用 `run` 隐式删原件。源文件变化、符号链接、缺少身份、输入错误等按错误或跳过记录，不能误当不合格删掉。全部缺测时 B02 仍返回 `empty`，B03 给出明确拒绝，B04 可交付空目录并在 `run` 中删除这些拒绝项；统计失败则整批禁止删除。

## 本机验证

完整结果：**77 passed，15 skipped，0 failed**。运行日志与结构化证据随包放在 `validation/`。

- 原有可独立测试 19 项、两个 P0 回归 30 项继续通过。
- 严格串行测试 14 项继续通过，其中混合批次的阶段记录已扩展到四阶段：

```text
B01.start → B01.end → B02.start → B02.end
→ B03.start → B03.end → B04.start → B04.end
```

- 新增 `tests/test_delivery.py` 14 项：B03 不写交付/删除产物、B04 独立重载不重跑前三阶段、中文目录和同名文件、完整字节和 SHA256 对应、非硬链接、交付先于删除、第一/第二份复制中途失败、源文件在判定后或复制中变化、拒绝文件变化保留、替换成符号链接、交接文件被修改、判定字段矛盾、已有目录拒绝覆盖、重复执行拒绝、交付审计失败禁止删除。

测试使用真实编码的临时 MCAP/CDR、真实复制及删除保护逻辑；缺失的上游统计和 S1–S4 数值函数使用明确的替身。没有复制或删除真实业务数据。

15 项真实引擎/ABC 测试因没有上游 `SLM_data_curation` 而跳过。真实数值正确性、多进程统计钩子和 ABC 适配仍需同事环境验证，新增 B04 不消除这些依赖。ABC 的 `to_quality_timeline(standard)` 接入要求保持不变。

## 同事验证

```bash
python3 -m pip install -e /path/to/SLM_data_curation/qwen-manip-preprocess
python3 -m pip install -e /path/to/stage-b-v4-result-delivery
SLM_UPSTREAM_REPO=/path/to/SLM_data_curation python3 -m pytest -q -rs tests

python3 run.py quality --config configs/jindian_marvin.yaml \
  --repo-root /path/to/SLM_data_curation \
  --input-root /path/to/input-mcaps \
  --output-root /path/to/reports/four-stage-001
```

先确认此前跳过的 15 项实际运行，并检查单 worker 与配置的 2 workers 都消费标准数据；再核对真实 `quality` 的统计人口、质量判定和 `accepted/` 文件。`run` 的删除验证只用独立复制的测试样本。完整接口见 [OPERATOR_IO.md](OPERATOR_IO.md)。

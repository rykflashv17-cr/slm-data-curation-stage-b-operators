# Stage B 通用容器（构建配方）

这个 Dockerfile 用于 `stage_b_operators_v2` 与**完整的**私有
`StridingAI/SLM_data_curation` checkout。它尚未构建或完成容器内验收；
2026-10-20 构建的 `egostandard-stage-b-operators` 镜像属于旧版 v1，
不适用于这里的其他数据集。

镜像只封装 Stage B：从 Stage A 的 `pseudo_v3` 输入做质量筛选及导出。
Stage A 的原始格式读取、字段映射与几何对齐仍由原仓库执行。完整用法见
[`../docs/USAGE.md`](../docs/USAGE.md)。

## 在本机准备构建上下文

`Dockerfile` 需要同一构建上下文中的两个目录：

```text
build-context/
├── upstream/SLM_data_curation/     # 完整私有仓库 checkout
└── stage_b_operators_v2/           # 本目录的代码
```

不要用工作区旧的 `upstream/SLM_data_curation` 快照：它只包含
EgoStandard。下面以一个单独的临时目录准备上下文，不把上游源码提交到
公开的算子仓库：

```bash
export SLM_DATA_CURATION_ROOT=/path/to/full/SLM_data_curation
export OPERATORS_DIR=/path/to/stage_b_operators_v2
BUILD_CONTEXT=$(mktemp -d)
mkdir -p "$BUILD_CONTEXT/upstream/SLM_data_curation" "$BUILD_CONTEXT/stage_b_operators_v2"
rsync -a --exclude=.git "$SLM_DATA_CURATION_ROOT/" "$BUILD_CONTEXT/upstream/SLM_data_curation/"
rsync -a --exclude=.git "$OPERATORS_DIR/" "$BUILD_CONTEXT/stage_b_operators_v2/"
cp "$OPERATORS_DIR/container/build.ignore" "$BUILD_CONTEXT/.dockerignore"
```

构建前核对 12 个 Stage B 配置均在上下文中。`UPSTREAM_REV` 应填实际
checkout 的提交号；镜像标签仅记录该值，不能代替源码一致性检查。

```bash
find "$BUILD_CONTEXT/upstream/SLM_data_curation/datasets" -name run_config_stage_b.yaml | wc -l
docker build \
  -f "$BUILD_CONTEXT/stage_b_operators_v2/container/Dockerfile" \
  --build-arg "UPSTREAM_REV=$(git -C "$SLM_DATA_CURATION_ROOT" rev-parse HEAD)" \
  --build-arg "OPERATORS_REV=$(git -C "$OPERATORS_DIR" rev-parse HEAD)" \
  -t slm-stage-b-operators:local "$BUILD_CONTEXT"
```

`Dockerfile` 会检查上游源码至少含 12 份 Stage B 配置，并验证
FFmpeg 提供 `libx264`。基础镜像为 Python 3.12 slim；Debian 的 FFmpeg
版本及所有数据集的实际导出结果仍需在目标环境验证。

## 容器调用

以下示例选择 EgoStandard。输入是**该数据集 Stage A 的 pseudo_v3
目录**；换数据集时同时替换 `--dataset` 和输入目录。

```bash
docker run --rm \
  -v /path/to/stage_a_aligned:/data/input:ro \
  -v /path/to/output:/data/output \
  slm-stage-b-operators:local \
  quality --dataset Egostandard \
  --input-root /data/input --output-dir /data/output/report \
  --sample-size 2 --workers 2
```

```bash
docker run --rm \
  -v /path/to/stage_a_aligned:/data/input:ro \
  -v /path/to/output:/data/output \
  slm-stage-b-operators:local \
  run --dataset Egostandard \
  --input-root /data/input --output-dir /data/output/clean \
  --sample-size 2 --workers 2 --export-workers 2
```

镜像包含完整的私有上游源码，只应存放在获准访问该源码的镜像仓库或主机。

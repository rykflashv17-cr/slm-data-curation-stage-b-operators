# Stage B 容器镜像

十个算子共用一份 CPU 运行镜像，入口为 `run.py`。镜像包含本目录的算子代码、
`SLM_data_curation` 的固定源码快照、Python 依赖、FFmpeg/ffprobe（含 libx264）。
它不包含 Stage A 数据、运行输出或基准测试数据。`quality` 调用算子 1–8；
`run` 继续调用导出算子 9；`merge` 调用归并算子 10。

## 已构建镜像（2026-10-20）

- 平台：Linux/amd64；Python 3.12；Debian Bookworm；FFmpeg 5.1.9。
- 镜像标签：`localhost/egostandard-stage-b-operators:20261020`。
- 镜像 ID：`ab4daa5e999df2190cc11903da8f1444d56336686ad1f918a305c6fb8630a41e`。
- 镜像大小：1.05 GB；压缩 Docker archive：375 MB。
- 上游源码：`0e694dbc9706b2a5bac8e550f8cd9de18f5b7473`；算子运行代码：`d93510f`。
- 归档：`/mnt/pfs/Data/ryk/slm_data_curation/images/egostandard-stage-b-operators_20261020_amd64.tar.gz`。
- 归档 SHA-256：`51d99d80b92986da095bd659b4ce4b76e22c76a0121c6443ad9374ca9635edcb`。

加载归档（Docker 或 Podman 均可）：

```bash
docker load -i /mnt/pfs/Data/ryk/slm_data_curation/images/egostandard-stage-b-operators_20261020_amd64.tar.gz
```

## 重新构建

在 `slm_data_curation/` 目录构建，构建上下文必须包含同级的
`upstream/SLM_data_curation/` 和 `stage_b_operators/`：

```bash
docker build --pull \
  -f stage_b_operators/container/Dockerfile \
  --build-arg UPSTREAM_REV=0e694dbc9706b2a5bac8e550f8cd9de18f5b7473 \
  --build-arg OPERATORS_REV=d93510f \
  -t localhost/egostandard-stage-b-operators:20261020 .
```

Docker 构建时会将整个上下文发送给构建器；可以将 `container/build.ignore`
复制为构建上下文根目录的 `.dockerignore` 以排除测试输出。开发机内层
Podman 的实测构建命令如下（仍在 `slm_data_curation/` 中执行）：

```bash
podman build --isolation chroot --format docker \
  --ignorefile stage_b_operators/container/build.ignore \
  -f stage_b_operators/container/Dockerfile \
  --build-arg APT_MIRROR=https://mirrors.aliyun.com \
  --build-arg UPSTREAM_REV=0e694dbc9706b2a5bac8e550f8cd9de18f5b7473 \
  --build-arg OPERATORS_REV=d93510f \
  -t localhost/egostandard-stage-b-operators:20261020 .
```

`--isolation chroot` 和镜像源参数只是开发机环境选项。

## 运行

以下示例使用 Docker。将宿主机 Stage A 数据只读挂载为 `/data/input`，
将输出目录挂载为 `/data/output`：

```bash
docker run --rm \
  -v /path/to/stage_a_aligned:/data/input:ro \
  -v /path/to/output:/data/output \
  localhost/egostandard-stage-b-operators:20261020 \
  quality --input-root /data/input --output-dir /data/output/quality \
  --sample-size 1 --workers 1
```

```bash
docker run --rm \
  -v /path/to/stage_a_aligned:/data/input:ro \
  -v /path/to/output:/data/output \
  localhost/egostandard-stage-b-operators:20261020 \
  run --input-root /data/input --output-dir /data/output/shard_0 \
  --sample-size 1 --workers 1 --export-workers 1 --output-mode filter \
  --shard-id 0 --num-shards 1 --export-shard-only
```

```bash
docker run --rm \
  -v /path/to/output:/data/output \
  localhost/egostandard-stage-b-operators:20261020 \
  merge --config /data/output/shard_0/operator_runtime_config.yaml \
  --shard-dir /data/output/shard_0 --output-dir /data/output/merged \
  --merge-workers 1 --relative-stats-workers 1
```

全量分片运行的统计缓存与分片参数见上级 `README.md`。开发机内层 Podman
运行这些命令时，需要将 `docker run` 换成 `podman run --cgroups=disabled
--network host`。

## 实测验收

2026-10-20 在开发机用真实 Stage A 的 Episode 0 做了上述三个阶段的
容器内验收。结果位于
`/mnt/pfs/Data/ryk/slm_data_curation/container_smoke_20261020/`：
质量报告生成成功，分片导出和归并均退出 0；最终 parquet 为 739 行，
两路 MP4 各为 739 帧。该测试只覆盖单条样本，不代表全量处理结果。

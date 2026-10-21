FROM python:3.12-slim-bookworm

ARG UPSTREAM_REV=unknown
ARG OPERATORS_REV=unknown
ARG APT_MIRROR=http://deb.debian.org
LABEL org.opencontainers.image.title="SLM data curation Stage B operators" \
      org.opencontainers.image.description="Dataset-selectable Stage B operators for SLM_data_curation" \
      org.opencontainers.image.source="https://github.com/rykflashv17-cr/slm-data-curation-stage-b-operators" \
      org.opencontainers.image.revision="${OPERATORS_REV}" \
      ai.striding.slm_data_curation.revision="${UPSTREAM_REV}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/slm \
    SLM_DATA_CURATION_ROOT=/opt/slm/upstream/SLM_data_curation

RUN sed -i "s|http://deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY stage_b_operators_v2/container/requirements-runtime.txt /tmp/requirements-runtime.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements-runtime.txt

WORKDIR /opt/slm
# The build context must contain a full, separately checked-out private upstream
# tree. Do not substitute the historical EgoStandard-only source snapshot.
COPY upstream/SLM_data_curation /opt/slm/upstream/SLM_data_curation
COPY stage_b_operators_v2/__init__.py stage_b_operators_v2/contracts.py \
     stage_b_operators_v2/pipeline.py stage_b_operators_v2/run.py \
     stage_b_operators_v2/operators.yaml /opt/slm/stage_b_operators/
COPY stage_b_operators_v2/operators /opt/slm/stage_b_operators/operators

RUN python -m pip install --no-cache-dir --no-deps \
        /opt/slm/upstream/SLM_data_curation/qwen-manip-preprocess \
    && python -c "from pathlib import Path; p=Path('/opt/slm/upstream/SLM_data_curation/datasets'); n=len(list(p.rglob('run_config_stage_b.yaml'))); assert n >= 12, f'full upstream checkout required, found only {n} Stage B packages'" \
    && python -c "import numpy, pandas, pyarrow, scipy, yaml, tqdm, robot_data_processing, stage_b_operators" \
    && ffprobe -version >/dev/null \
    && ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libx264

WORKDIR /opt/slm/stage_b_operators
ENTRYPOINT ["python", "/opt/slm/stage_b_operators/run.py"]

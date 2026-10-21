FROM python:3.12-slim-bookworm

ARG UPSTREAM_REV=unknown
ARG OPERATORS_REV=unknown
ARG APT_MIRROR=http://deb.debian.org
LABEL org.opencontainers.image.title="EgoStandard Stage B operators" \
      org.opencontainers.image.description="Ten CPU operators for EgoStandard Stage B" \
      org.opencontainers.image.source="https://github.com/rykflashv17-cr/slm-data-curation-stage-b-operators" \
      org.opencontainers.image.revision="${OPERATORS_REV}" \
      ai.striding.slm_data_curation.revision="${UPSTREAM_REV}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/slm

RUN sed -i "s|http://deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY stage_b_operators/container/requirements-runtime.txt /tmp/requirements-runtime.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements-runtime.txt

WORKDIR /opt/slm
COPY upstream/SLM_data_curation /opt/slm/upstream/SLM_data_curation
COPY stage_b_operators/__init__.py stage_b_operators/contracts.py \
     stage_b_operators/pipeline.py stage_b_operators/run.py \
     stage_b_operators/operators.yaml /opt/slm/stage_b_operators/
COPY stage_b_operators/operators /opt/slm/stage_b_operators/operators

RUN python -m pip install --no-cache-dir --no-deps \
        /opt/slm/upstream/SLM_data_curation/qwen-manip-preprocess \
    && python -c "import numpy, pandas, pyarrow, scipy, yaml, tqdm, robot_data_processing, stage_b_operators" \
    && ffprobe -version >/dev/null \
    && ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libx264

WORKDIR /opt/slm/stage_b_operators
ENTRYPOINT ["python", "/opt/slm/stage_b_operators/run.py"]

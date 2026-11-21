# LIBERO-Plus simulator image, built locally by `evoab setup --integration libero_plus --container`.
#
# Adapted from docker/Dockerfile.libero_plus in allenai/vla-evaluation-harness
# (Apache-2.0), whose published image is not publicly pullable. The base image is
# public. EvoAB adds its runtime in a separate local layer (see container.toml).
FROM ghcr.io/allenai/vla-evaluation-harness/base@sha256:9198417200a2675b436b33e454dc75103c375bfee11b22bba8eb07380674c07c

ARG HF_ENDPOINT
ARG PIP_INDEX_URL
ARG UV_INDEX_URL
ARG UV_DEFAULT_INDEX
ARG UBUNTU_MIRROR

RUN conda create -n libero python=3.11 pip -y && conda clean -afy
SHELL ["conda", "run", "-n", "libero", "/bin/bash", "-c"]

# ImageMagick/Wand for LIBERO-Plus texture perturbations.
USER root
RUN if [ -n "${UBUNTU_MIRROR}" ]; then \
        sed -i "s@http://archive.ubuntu.com/ubuntu@http://${UBUNTU_MIRROR}/ubuntu@g; s@http://security.ubuntu.com/ubuntu@http://${UBUNTU_MIRROR}/ubuntu@g" /etc/apt/sources.list; \
    fi && apt-get update && apt-get install -y --no-install-recommends \
        libmagickwand-dev libfontconfig1-dev libexpat1 unzip wget \
    && rm -rf /var/lib/apt/lists/*

# The base image's uv config sets an upload-date cutoff (exclude-newer). PyPI mirrors
# omit upload dates, so with a mirror uv would see no versions at all: drop the
# config then and rely on the exact pins below. Without a mirror the cutoff stays.
RUN uv pip install --no-cache-dir $( [ -n "${UV_DEFAULT_INDEX}${UV_INDEX_URL}" ] && echo --no-config ) \
        "torch==2.1.0" --torch-backend cpu
# Exact versions keep resolution identical on any index or mirror (an unpinned
# resolve through a mirror once picked an unbuildable matplotlib 1.4.3).
RUN uv pip install --no-cache-dir $( [ -n "${UV_DEFAULT_INDEX}${UV_INDEX_URL}" ] && echo --no-config ) \
        numpy==1.24.4 numba==0.58.1 scipy==1.10.1 mujoco==3.2.3 robosuite==1.4.0 bddl==1.0.1 \
        pillow==12.3.0 h5py==3.16.0 PyYAML==6.0.3 termcolor==3.3.0 future==1.0.0 easydict==1.13 hydra-core==1.3.4 \
        gym==0.26.2 matplotlib==3.10.9 opencv-python==4.11.0.86 scikit-image==0.24.0 Wand==0.7.2 \
    && rm -rf ~/.cache/pip ~/.cache/uv

# The pinned LIBERO-Plus revision EvoAB's integration requires.
# Fetched on the host by setup (see [[sources]] in container.toml), so the build needs no GitHub access.
COPY LIBERO-plus /app/LIBERO-plus
RUN cd /app/LIBERO-plus \
    && uv pip install --no-cache-dir $( [ -n "${UV_DEFAULT_INDEX}${UV_INDEX_URL}" ] && echo --no-config ) \
        --config-setting editable_mode=compat -e . \
    && rm -rf ~/.cache/uv

# MuJoCo assets (about 6.4 GB) from the MIT-licensed dataset Sylvest/LIBERO-plus at a pinned revision.
ARG ASSETS_REVISION=dd2bd61b7d9a6fef1abc52d606e983b41886a149
RUN wget --progress=dot:giga \
        "${HF_ENDPOINT:-https://huggingface.co}/datasets/Sylvest/LIBERO-plus/resolve/${ASSETS_REVISION}/assets.zip" \
        -O /tmp/assets.zip \
    && unzip -q /tmp/assets.zip -d /tmp/assets-extracted && rm /tmp/assets.zip \
    && mv "$(find /tmp/assets-extracted -type d -name assets -print -quit)" /app/LIBERO-plus/libero/libero/assets \
    && rm -rf /tmp/assets-extracted

WORKDIR /workspace

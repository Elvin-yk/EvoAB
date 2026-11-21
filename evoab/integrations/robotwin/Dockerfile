# RoboTwin 2.0 simulator image, built locally by `evoab setup --integration robotwin --container`.
#
# Adapted from docker/Dockerfile.robotwin in allenai/vla-evaluation-harness
# (Apache-2.0). Their published image pins an older RoboTwin revision and a
# CUDA 12.1 PyTorch that cannot run on Blackwell GPUs, so this recipe builds the
# revision EvoAB pins with PyTorch 2.7 / CUDA 12.8 and CuRobo compiled up to SM 12.0.
# The base image is public. EvoAB adds its runtime in a separate local layer.
FROM ghcr.io/allenai/vla-evaluation-harness/base@sha256:9198417200a2675b436b33e454dc75103c375bfee11b22bba8eb07380674c07c

ARG HF_ENDPOINT
ARG PIP_INDEX_URL
ARG UV_INDEX_URL
ARG UV_DEFAULT_INDEX
ARG UBUNTU_MIRROR

# nvcc and headers for CuRobo's CUDA extensions (the base image only has the
# runtime), taken from NVIDIA's public devel image of the same Ubuntu release.
COPY --from=nvidia/cuda:12.8.1-devel-ubuntu22.04 /usr/local/cuda-12.8 /usr/local/cuda-12.8
ENV CUDA_HOME=/usr/local/cuda-12.8
ENV PATH=/usr/local/cuda-12.8/bin:$PATH

RUN conda create -n robotwin python=3.11 pip -y && conda clean -afy
SHELL ["conda", "run", "-n", "robotwin", "/bin/bash", "-c"]

# The base image's uv config sets an upload-date cutoff that PyPI mirrors cannot
# honour (no upload dates): drop it when a mirror is used; exact pins keep the
# resolution identical either way.
RUN uv pip install --no-cache-dir $( [ -n "${UV_DEFAULT_INDEX}${UV_INDEX_URL}" ] && echo --no-config ) \
        "torch==2.7.0" "torchvision==0.22.0" --torch-backend cu128
# sapien imports pkg_resources (removed from setuptools 70+).
RUN uv pip install --no-cache-dir $( [ -n "${UV_DEFAULT_INDEX}${UV_INDEX_URL}" ] && echo --no-config ) \
        "setuptools==69.5.1" wheel==0.45.1 \
        numpy==1.26.4 opencv-python-headless==4.10.0.84 transforms3d==0.4.2 sapien==3.0.0b1 scipy==1.10.1 \
        mplib==0.2.1 toppra==0.6.9 gymnasium==0.29.1 trimesh==4.4.3 open3d==0.18.0 imageio==2.34.2 \
        pydantic==2.12.5 PyYAML==6.0.3 huggingface_hub==0.25.0 h5py==3.16.0 rich==14.2.0 termcolor==3.3.0 \
        matplotlib==3.10.9 pillow==12.3.0 tqdm==4.67.1 warp-lang==1.12.0 ninja==1.13.0 \
    && rm -rf ~/.cache/uv

# Upstream patches from scripts/_install.sh: UTF-8 URDF reading in SAPIEN and
# mplib's collision check during screw motions.
RUN SAPIEN_LOC=$(python -c "import sapien, os; print(os.path.dirname(sapien.__file__))") \
    && sed -i -E 's/("r")(\))( as)/\1, encoding="utf-8") as/g' "$SAPIEN_LOC/wrapper/urdf_loader.py" \
    && MPLIB_LOC=$(python -c "import mplib, os; print(os.path.dirname(mplib.__file__))") \
    && sed -i -E 's/(if np.linalg.norm\(delta_twist\) < 1e-4 )(or collide )(or not within_joint_limit:)/\1\3/g' "$MPLIB_LOC/planner.py"

# The pinned RoboTwin revision and CuRobo release, fetched on the host by setup
# (see [[sources]] in container.toml) so the build needs no GitHub access.
COPY RoboTwin /app/RoboTwin
COPY curobo /app/RoboTwin/envs/curobo

# CuRobo motion planner. No GPU is visible while building, so name the architectures.
ENV TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0;12.0+PTX"
RUN cd /app/RoboTwin/envs/curobo \
    && MAX_JOBS=8 uv pip install --no-cache-dir $( [ -n "${UV_DEFAULT_INDEX}${UV_INDEX_URL}" ] && echo --no-config ) \
        --no-build-isolation -e .

# Assets (objects, embodiments, textures) from the dataset TianxingChen/RoboTwin2.0,
# as upstream's assets/_download.py fetches them.
RUN cd /app/RoboTwin/assets && python _download.py \
    && unzip -qo objects.zip && rm -f objects.zip \
    && unzip -qo embodiments.zip && rm -f embodiments.zip \
    && unzip -qo background_texture.zip && rm -f background_texture.zip \
    && rm -rf .cache \
    && cd /app/RoboTwin && python scripts/update_embodiment_config_path.py

WORKDIR /workspace

# MetaWorld MT50 image, built locally by `evoab setup --integration metaworld`.
# CPU only: MuJoCo renders through OSMesa. EvoAB adds its dependencies in a separate local layer and mounts its code at run time.
FROM python:3.11-slim-bookworm

ARG PIP_INDEX_URL
ARG UV_INDEX_URL
ARG UV_DEFAULT_INDEX

# UBUNTU_MIRROR is the distribution mirror host builds use (it serves Debian too).
ARG UBUNTU_MIRROR

RUN if [ -n "${UBUNTU_MIRROR}" ]; then \
        sed -i "s@http://deb.debian.org@http://${UBUNTU_MIRROR}@g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends libosmesa6 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "metaworld==3.1.1" "PyOpenGL==3.1.9" packaging

WORKDIR /workspace

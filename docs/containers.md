# Container runtimes

A benchmark can run from a Docker image instead of a native installation. EvoAB
does not host images: an integration either references a public upstream image
pinned by digest, or ships a build recipe that the user's machine builds once.

| Integration | Source | Image or recipe |
|---|---|---|
| LIBERO-PRO | public image | `ghcr.io/allenai/vla-evaluation-harness/libero-pro` (pinned digest) plus the official perturbation suites from `zhouxueyang/LIBERO-Pro` |
| LIBERO-Plus | local build | `evoab/integrations/libero_plus/Dockerfile` (based on allenai's public base image; their LIBERO-Plus image is not public) |
| RoboDojo | local build | the Dockerfile in RoboDojo's pinned upstream checkout (Isaac Sim 5.1); requires accepting the NVIDIA Isaac Sim EULA; assets (~38.5 GiB) downloaded on the host and mounted |

## A new machine, step by step

1. Install Docker Engine and the NVIDIA Container Toolkit
   (`sudo nvidia-ctk runtime configure --runtime=docker`, restart Docker), and
   add the user to the `docker` group. `docker run --rm --gpus all
   nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi` must work.
2. Install EvoAB with the agent runtime and start Studio:
   `pip install -e '.[agent]'`, then `evoab studio`.
3. In Studio, 设置 → 模型: add a provider and at least one model preset.
4. 设置 → 评测集: the 部署条件 panel checks Docker access, GPU containers, disk
   space (120 GiB recommended) and model presets, with the fix for anything
   missing. On a network in mainland China choose 构建镜像源 → 清华镜像 + hf-mirror.
5. Click 一键部署（Docker） (or 构建并部署) on a benchmark. Progress appears in the
   card; when it reads 已就绪 · Docker, start runs from 发起评测 as usual.

No runtime wheel is needed: when 运行时安装包 is empty, the local layer installs
the exact `evoab` package Studio runs plus its service dependencies. A wheel
set there takes precedence. `evoab benchmarks check` and
`evoab benchmarks prepare INTEGRATION --container` do the same from a shell.

## Prepare and run

```bash
evoab benchmarks prepare libero_pro --container      # or: evoab setup --integration libero_pro --container --wheel WHEEL
evoab run --integration libero_pro --deploy local --container --runtime-profile libero_object --task TASK ...
```

In Studio, each benchmark with a container recipe shows 一键部署（Docker） (or
构建并部署 for local builds). Once a container runtime is ready, Studio launches
that benchmark's runs in it and marks the runtime "Docker".

Setup pulls (or builds) the upstream image, then builds a small local layer
`evoab/<integration>:<version>-<hash>` that adds the runtime wheel and the
recipe's `prepare` commands (for example recorded native revisions or extra
task assets), lists the tasks inside the image and verifies the pinned native
revision. Each run starts `docker run --rm --init --network host --gpus …` with the
host user's identity and only the run directory mounted, at the same path, so
service URLs and artifacts match native runs; the container is removed when
the run ends.

Builds use the host network. The 构建镜像源 setting forwards apt, PyPI, conda and
Hugging Face mirrors to every build (upstream recipes such as RoboDojo's accept
the same arguments); `HF_ENDPOINT` alone overrides the asset source, and
`EVOAB_CONTAINER_BUILD_PROXY=1` forwards the host's proxy variables into builds.
Pulling the public images needs Docker itself to reach ghcr.io (configure the
Docker daemon's proxy or registry mirror if required).

## Adding a recipe

Put `container.toml` next to the integration's `deployment.py`:

```toml
image = "registry/repo@sha256:…"     # or: dockerfile = "Dockerfile" (in the package)
python = "/opt/conda/envs/sim/bin/python"
native_root = "/app/native"          # passed to the integration as --native-root
gpu = true
prepare = ["printf %s <revision> > /app/native/.evoab-revision"]

[environment]
MUJOCO_GL = "egl"
```

The integration's own `external_data`, `verify` and `plan` run unchanged: the
runtime interpreter is recorded as `docker://<local image>#<python>`, and
queries and launches are executed inside the image. Images that drop `.git`
record the pinned revision in `.evoab-revision`, which the native-source check
accepts in place of a checkout.

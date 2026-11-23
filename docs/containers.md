# Container runtimes

A benchmark can run from a Docker image instead of a native installation. EvoAB
does not host images: an integration either references a public upstream image
pinned by digest, or ships a build recipe that the user's machine builds once.

| Integration | Source | Image or recipe |
|---|---|---|
| LIBERO | public image | [AllenAI original LIBERO image recipe](https://github.com/allenai/vla-evaluation-harness/blob/505ae54c1c9ba07cb3aec82ce2808402acd43035/docker/Dockerfile.libero), pinned by digest |
| LIBERO-PRO | public image | `ghcr.io/allenai/vla-evaluation-harness/libero-pro` (pinned digest) plus the official perturbation suites from `zhouxueyang/LIBERO-Pro` |
| LIBERO-Plus | local build | `evoab/integrations/libero_plus/Dockerfile` (based on allenai's public base image; their LIBERO-Plus image is not public) |
| CALVIN | public image | `ghcr.io/allenai/vla-evaluation-harness/calvin` (pinned digest; same `calvin` / `calvin_env` revisions as EvoAB) |
| RoboTwin 2.0 | local build | `evoab/integrations/robotwin/Dockerfile` (pinned revision, PyTorch 2.8 / CUDA 12.8, CuRobo for SM 8.0–12.0; allenai's image pins an older revision) |
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
   missing. Its 下载源 row shows which package and model sources builds use.
5. Click 部署 on a benchmark. Progress appears in the card. Deployment ends by
   starting one task's environment service once (`evoab setup --check-launch`);
   only then does the card read 已就绪 with an 已部署 button. Start runs from 发起任务.
   A runtime built from an older container recipe shows 未部署 again.

No runtime wheel is needed: the local layer installs the exact `evoab` package
Studio runs plus its service dependencies (`evoab setup --wheel` still accepts a
runtime wheel for development). `evoab benchmarks check` and
`evoab benchmarks prepare INTEGRATION --container` do the same from a shell.

## Prepare and run

```bash
evoab benchmarks prepare libero_pro --container      # or: evoab setup --integration libero_pro --container --wheel WHEEL
evoab run --integration libero_pro --deploy local --container --runtime-profile libero_object --task TASK ...
```

For original LIBERO, select **LIBERO** in Studio or use:

```bash
evoab benchmarks prepare libero --container
evoab run --integration libero --deploy local --container --runtime-profile libero_object \
  --task pick_up_the_alphabet_soup_and_place_it_in_the_basket --init-state-index 0 \
  --model YOUR_MODEL_PRESET --no-sam3
```

Spatial, Object, Goal and Long (`libero_10`) have 10 tasks each, with respective
step budgets of 220, 280, 300 and 520. The separate `libero_90` suite uses 400
steps. Select an official starting state by index (default 0); the seed controls
the simulator separately. Plus and PRO use forked sources and separate runtimes.

Studio lists only benchmarks it can deploy in one step, each with one status
(未部署, 部署中, 部署失败, 已就绪) and one button: 部署, or 已部署 once ready (runs
start from 发起任务). Docker deployments add 卸载 beside it, disabled until a
Docker runtime is installed (an adopted native installation is left alone; see
[uninstall](#uninstall-a-deployment)).
Benchmarks with a container recipe deploy as Docker runtimes; MetaWorld, which
has none, uses its native setup recipe. Once a container runtime is ready,
Studio launches that benchmark's runs in it.

Setup pulls (or builds) the upstream image, then builds a small local layer
`evoab/<integration>:<version>-<hash>` that adds the runtime wheel and the
recipe's `prepare` commands (for example recorded native revisions or extra
task assets), lists the tasks inside the image and verifies the pinned native
revision. Each run starts `docker run --rm --init --network host --gpus …` with the
host user's identity and only the run directory mounted, at the same path, so
service URLs and artifacts match native runs; the container is removed when
the run ends.

Builds use the host network. Studio picks their download sources itself: when
huggingface.co does not answer directly (as in mainland China), it forwards the
Tsinghua apt, PyPI and conda mirrors and hf-mirror to every build (upstream
recipes such as RoboDojo's accept the same arguments). Setting any of
`UBUNTU_MIRROR`, `PIP_INDEX_URL`, `UV_DEFAULT_INDEX`, `MINICONDA_URL`,
`CONDA_CHANNEL_MIRROR` or `HF_ENDPOINT` in Studio's environment replaces that
choice with exactly those variables, and
`EVOAB_CONTAINER_BUILD_PROXY=1` forwards the host's proxy variables into builds
(only for a proxy that does not intercept TLS: containers do not trust a host CA).
Git sources a recipe declares under `[[sources]]` are fetched on the host with
its own network settings, so builds never need to reach GitHub.
Pulling the public images needs Docker itself to reach ghcr.io (configure the
Docker daemon's proxy or registry mirror if required).

## Import a local image archive

The directory belongs to the machine running Studio, not to the browser that
opens it. Copy a single-image archive made with `docker save` there, finish
copying, refresh the file list, select the target benchmark and archive, then
import. Placing a file alone does not make a benchmark ready: import and its
validation are required, and EvoAB's runtime layer may still need network access.
Supported extensions are `.tar`, `.tar.gz`, `.tgz`, `.tar.bz2` and `.tar.xz`.

The directory defaults to `<EVOAB_CACHE_DIR or ~/.cache/evoab>/image-imports/`.
**本地镜像导入** in benchmark settings shows the expanded server path and
lets the directory be edited, saved, or reset to that default; the choice is
stored in Studio's settings and reused after a refresh or restart. Relative paths
are rejected and the previous directory stays active when saving fails. Changing
the directory neither moves nor deletes existing archives. The setting covers
Studio's file list and the imports Studio starts; a CLI `evoab setup` reads
`EVOAB_IMAGE_IMPORT_DIR` or the cache default instead, so pass the same path
there.

For scripts and the CLI, `EVOAB_IMAGE_IMPORT_DIR` selects the directory for one
invocation (precedence: explicit Studio setting or setup job, then
`EVOAB_IMAGE_IMPORT_DIR`, then the cache default). Studio passes the directory
resolved at import time to its setup child, so editing the setting afterwards
does not redirect a running job. The CLI uses the same pipeline as Studio:

```bash
EVOAB_IMAGE_IMPORT_DIR=/data/evoab-images evoab setup --integration libero_plus --container --image-archive libero-plus.tar
```

The archive name must be a plain filename directly inside that directory; paths
and symlinks are refused.

Import accepts one Linux image matching the host CPU architecture. It loads the
archive through Docker and selects the resulting image by its content ID.
An EvoAB integration label, when present, must match the selected benchmark.
The imported image replaces that benchmark's upstream pull or base-image build.
EvoAB still builds its runtime layer, so dependency downloads may be needed.
For the public CALVIN and LIBERO-PRO images, the archive's config digest must
match the image ID recorded from the pinned upstream manifest to run that
recipe's preparation commands. This comparison needs no registry request.
Other imported images must already include the pinned native source (a clean Git
checkout or an existing matching revision marker) and task assets. Exporting a
successfully prepared EvoAB image is supported. Raw upstream images that omit
source revision evidence or task resources fail validation. Import never stamps
an unverified native source with the expected revision or runs the recipe's
source/resource preparation commands inside an unrecognized image. RoboDojo still needs its
host-side source and assets.

The existing native revision, dependency and task checks run on the resulting
runtime. Import always performs the normal environment launch check while holding
the setup lock. Studio offers the imported runtime only after that check passes;
a failed import shows its error in the benchmark's setup log. Changing a filename
or image tag cannot bypass these checks. These are compatibility checks for
trusted simulator images, not a security audit of arbitrary executable code.

Docker owns the installed image storage. The import directory holds source
archives; importing leaves them available for retries. After successful
validation, the source archive can be removed manually to recover disk space.

## Uninstall a deployment

A prepared Docker deployment shows 卸载 next to 部署 / 已部署. Uninstalling
removes this deployment only: its records (`runtime.json`, `validation.json`,
`setup_failure.json`) and the local runtime image `evoab/<integration>:…` (or the
imported image's built layer, referenced by content ID). Everything else stays:
completed and historical runs with their trajectories, the image archives under
the import directory, upstream and base images, build caches, and the host assets
prepared under the runtime directory (RoboDojo's assets, launch checks, previews).
Redeploying afterwards pulls or rebuilds as usual, reusing whatever is still
cached. The setup log is kept; only its failed status is cleared, so a later
successful deployment is not shown as failed.

Removal refuses rather than interrupts work:

- an active setup for the same integration (the runtime's `.prepare.lock` is
  respected, so a CLI `evoab setup --container` also blocks it),
- an active Studio run of that integration,
- any container, running or stopped, that still uses the image.

Docker is then asked to remove the image without `--force`; EvoAB never prunes
images or stops containers. Each Docker call is bounded (60 s), so an
unresponsive daemon fails the request instead of blocking deployment and launch.
If the removal fails, the deployment records are left untouched and the error is
reported. If the image was already removed
outside EvoAB, the records can still be unregistered once Docker positively says
the image does not exist — an unreachable daemon is not treated as a missing
image. An image another recorded deployment still references is kept, and the
response reports that it was retained. Uninstalling only covers Docker
deployments: adopted native installations are left alone. Studio serializes
uninstall against its own deploy and launch paths; a `docker run` started
concurrently from the CLI is protected only by Docker's non-force removal, not by
a global admission lock.

## Adding a recipe

Put `container.toml` next to the integration's `deployment.py`:

```toml
image = "registry/repo@sha256:…"     # or: dockerfile = "Dockerfile" (in the package)
python = "/opt/conda/envs/sim/bin/python"
native_root = "/app/native"          # passed to the integration as --native-root
gpu = true
prepare = ["printf %s <revision> > /app/native/.evoab-revision"]

[[sources]]                          # dockerfile builds only: fetched on the host,
path = "native"                      # then available to COPY in the Dockerfile
url = "https://github.com/org/native.git"
ref = "<commit or tag>"

[environment]
MUJOCO_GL = "egl"
```

The integration's own `external_data`, `verify` and `plan` run unchanged: the
runtime interpreter is recorded as `docker://<local image>#<python>`, and
queries and launches are executed inside the image. Images that drop `.git`
record the pinned revision in `.evoab-revision`, which the native-source check
accepts in place of a checkout.

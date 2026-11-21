# Installing EvoAB (instructions for a coding agent)

These steps are written for a coding agent (Claude Code, Codex, …) installing
EvoAB on a user's Linux machine. Work through them in order, check each result,
and report what you did. Ask the user before every command that needs `sudo`,
and never ask for, type or store API keys: the user enters them in Studio.

## 1. Check the machine

- Linux x86_64 with an NVIDIA GPU: `nvidia-smi` must list the GPU and driver.
- At least 120 GiB free disk space for benchmark images and assets.
- Note whether the machine reaches `github.com`, `pypi.org`, `ghcr.io` and
  `huggingface.co` directly. If it needs a proxy or is in mainland China, keep
  that in mind for steps 3–6.

## 2. Get the code

```bash
git clone https://github.com/Elvin-yk/EvoAB.git ~/evoab
```

The repository may be private. If cloning fails with an authentication error,
ask the user to run `gh auth login` (install the GitHub CLI first if needed),
then clone with `gh repo clone Elvin-yk/EvoAB ~/evoab`.

## 3. Install EvoAB

Use Python 3.11 in a dedicated virtual environment (with `uv` if available):

```bash
cd ~/evoab
uv venv --seed --python 3.11 .venv || python3.11 -m venv .venv
uv pip install --python .venv/bin/python -e '.[agent]' || .venv/bin/python -m pip install -e '.[agent]'
.venv/bin/evoab --help
```

If downloads fail with TLS errors such as `tls handshake eof`, the shell's proxy
variables may be breaking HTTPS to PyPI: test `curl https://pypi.org/simple/`
with and without them, and run the install without the proxy
(`env -u https_proxy -u http_proxy …`) when the direct connection works. In
mainland China, add `--index-url https://pypi.tuna.tsinghua.edu.cn/simple`
(or set `UV_DEFAULT_INDEX` / `PIP_INDEX_URL`) if PyPI is slow or unreachable.

## 4. Docker with GPU access

Benchmarks run in Docker containers. Skip what is already in place:

- Docker Engine (docs.docker.com/engine/install). The user must be in the
  `docker` group (`sudo usermod -aG docker $USER`, then a new login session).
- NVIDIA Container Toolkit (`nvidia-container-toolkit` from NVIDIA's apt
  repository), then `sudo nvidia-ctk runtime configure --runtime=docker` and
  restart Docker.
- Verify: `docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi`
  (retry once if the pull ends with a bare `EOF`; registry connections can drop).
- If `ghcr.io` or Docker Hub needs a proxy, configure the Docker daemon's proxy
  (systemd drop-in `HTTP_PROXY`/`HTTPS_PROXY`) or a registry mirror.

## 5. Start Studio

Run Studio so it survives the agent session, for example in tmux:

```bash
tmux new-session -d -s evoab-studio -c ~/evoab \
  ".venv/bin/evoab studio --runs-root ~/evoab-runs --port 8770"
curl -sf http://127.0.0.1:8770/api/config >/dev/null && echo Studio is up
```

If the shell sets `all_proxy` to a SOCKS proxy, start Studio with
`env -u all_proxy -u ALL_PROXY` in front of the command.

## 6. Check and hand over

Run `.venv/bin/evoab benchmarks check` and fix anything it reports, except the
model presets, which the user adds. Then tell the user:

1. Open `http://<host>:8770` (forward the port over SSH for a remote machine).
2. 设置 → 模型: add a provider with its API key and at least one model preset.
3. 设置 → 评测集: in mainland China choose 构建镜像源 → 清华镜像 + hf-mirror; then
   click 一键部署（Docker） on a benchmark and wait for 已就绪 · Docker.
4. 发起评测: choose the task, method and model, and start a run.

Report the Studio URL, what was installed, and any check that still fails.

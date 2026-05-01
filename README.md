# Docker Wrapper for Transparent Custom Registry

A Python CLI wrapper that intercepts `docker` commands and rewrites unqualified image references to use a custom registry.

## Why?

In highly restricted environments (e.g., air-gapped VMs), you often need to rely on a private registry.
[But Docker disallow changing the default registry](https://stackoverflow.com/questions/33054369/how-to-change-the-default-docker-registry-from-docker-io-to-my-private-registry).

As a result, you’re forced to prefix every image with your registry endpoint.
That works, but breaks portability in environments that *can’t* access that registry.

### My use case

- A personal project built by a [local AI agent on an air-gapped sandbox VM](https://github.com/adiffpirate/agent-sandbox)
- A local registry so the agent can pull project images
- CI running on GitHub Actions

If I modify my Dockerfiles to use the local registry, CI breaks.
If I don’t, the air-gapped environment breaks.

Editing every project to handle this isn’t scalable.

**This wrapper solves that.**
Install it once on the VM, and all `docker` commands automatically use the local registry.
No project changes required.

From the agent’s perspective, it’s just running Docker. The wrapper is completely transparent.

## Installation

Download the script and place it on the system `PATH`:

```bash
sudo mv "$(which docker)" /usr/bin/docker.real  # Move the real Docker binary
sudo curl -L https://github.com/adiffpirate/docker-wrapper-custom-registry/raw/refs/heads/main/docker.py -o /usr/local/bin/docker
sudo chmod +x /usr/local/bin/docker
sudo echo 'DOCKER_REGISTRY=<your_registry_endpoint>' >> /etc/environment
```

## Usage

Simply run `docker` as you normally would

```bash
docker <docker-args...>
```

> The wrapper expects the real Docker binary at `/usr/bin/docker.real`.
> You can override this path using the `DOCKER_REAL` environment variable.

## How it works

- Unqualified images are rewritten to `<registry>/<image>` (e.g., `python:3.11`->`<registry>/python:3.11`, `python`->`<registry>/python`)
- Qualified images (`localhost/foo`, `my.registry.io/bar`, `scratch`) are left unchanged
- BuildKit and Compose CLI builds are disabled via environment variables
- Rewrites occur in:
  - `FROM` instructions in Dockerfiles
  - `image:` fields in Compose files
  - The first image argument in `pull`, `run`, and `create`
- Temporary files are created for rewritten Dockerfiles/Compose files and cleaned up on exit

## Tests

```bash
make test             # Run all tests
make test-unit        # Unit tests only
make test-functional  # Functional tests only
```

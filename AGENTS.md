# AGENTS.md

## Repo: `docker-wrapper-custom-registry`

Single-file Python CLI wrapper (`docker.py`) that intercepts `docker` commands and rewrites image references to a local registry.

### What it does

- Replaces unqualified image names (e.g. `python:3.11`) with `10.0.2.100:5000/python:3.11`
- Leaves qualified refs (e.g. `localhost/foo`, `my.registry.io/bar`, `scratch`) untouched
- Disables BuildKit and Compose CLI build via env vars (`DOCKER_BUILDKIT=0`, `COMPOSE_DOCKER_CLI_BUILD=0`)
- Rewrites `FROM` lines in Dockerfiles, `image:` in compose files, and first image arg in `pull`/`run`/`create`
- Creates temp files alongside originals for rewritten Dockerfiles/compose files; cleans up on exit

### Key functions

| Function | Purpose |
|---|---|
| `rewrite(ref)` | Rewrites an unqualified image ref to the custom registry |
| `rewrite_first_image(args)` | Rewrites the first non-flag image argument in a command |
| `rewrite_dockerfile(path)` | Rewrites `FROM` lines; returns temp file path or original |
| `rewrite_compose_file(path)` | Rewrites `image:` fields in compose YAML; returns temp file path |
| `env_with_buildkit_off()` | Returns copy of env with BuildKit/Compose build disabled |

### Running

```
DOCKER_REGISTRY=10.0.2.100:5000 python3 docker.py <docker-args...>
```

It expects `/usr/bin/docker.real` to exist (the actual docker binary). Without args it execs `docker.real` directly.

### Constraints

- `DOCKER_REGISTRY` env var is **required** — the script exits with an error if not set
- Depends on `pyyaml` (optional import — falls back gracefully if absent, meaning compose rewriting silently skips)
- No test suite, no linter, no formatter
- Temp files use prefix `.FILENAME.rewritten.` in the same directory as the source

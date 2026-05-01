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
- Temp files use prefix `.FILENAME.rewritten.` in the same directory as the source
- `DOCKER_REAL` env var overrides the hardcoded `/usr/bin/docker.real` path (useful for tests)

### Tests

```bash
make test        # run all tests
make test-unit   # unit tests only
make test-functional  # functional tests only
```

- Unit tests: `tests/unit/test_docker.py` — test core functions in isolation
- Functional tests: `tests/functional/test_docker_wrapper.py` — run `docker.py` as subprocess against a mock `docker.real`

### Git

- Always use [conventional commits](https://www.conventionalcommits.org/) format: `type: description`
- Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`
- If git identity is not already configured, ask the user for their username and email before committing

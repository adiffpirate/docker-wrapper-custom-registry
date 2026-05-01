#!/usr/bin/env python3
import atexit
import os
import subprocess
import sys
import tempfile

try:
    import yaml
except Exception:
    yaml = None

REAL = os.environ.get("DOCKER_REAL", "/usr/bin/docker.real")
REGISTRY = os.environ.get("DOCKER_REGISTRY")
if not REGISTRY:
    print("Error: DOCKER_REGISTRY environment variable is not set.", file=sys.stderr)
    print("Set it to your local registry address, e.g.:", file=sys.stderr)
    print("  export DOCKER_REGISTRY=10.0.2.100:5000", file=sys.stderr)
    sys.exit(1)
TEMP_PATHS = []


def cleanup():
    for p in TEMP_PATHS:
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass


atexit.register(cleanup)


def is_flag(arg: str) -> bool:
    return arg.startswith("-") and arg != "-"


def is_qualified(ref: str) -> bool:
    if ref == "scratch":
        return True
    if "/" not in ref:
        return False
    head = ref.split("/", 1)[0]
    return head == "localhost" or "." in head or ":" in head


def rewrite(ref: str, registry: str = REGISTRY) -> str:
    if ref.startswith(registry + "/") or is_qualified(ref):
        return ref
    return f"{registry}/{ref}"


def rewrite_first_image(args, registry: str = REGISTRY):
    out = list(args)

    i = 0
    while i < len(out):
        token = out[i]

        # skip flags
        if is_flag(token):
            # flags with = have their value embedded (e.g. -e=FOO=BAR, --name=mycontainer)
            # so only advance by 1
            if "=" in token:
                i += 1
            # skip next token if it's a value (not another flag)
            elif i + 1 < len(out) and not is_flag(out[i + 1]):
                i += 2
            else:
                i += 1
            continue

        # first non-flag after skipping flag+value pairs = IMAGE
        out[i] = rewrite(out[i], registry)
        return out

    return out


def rewrite_dockerfile_text(text: str, registry: str = REGISTRY) -> str:
    out = []

    for line in text.splitlines(True):
        if not line.lstrip().upper().startswith("FROM "):
            out.append(line)
            continue

        newline = "\n" if line.endswith("\n") else ""
        core = line[:-1] if newline else line
        prefix_ws = core[: len(core) - len(core.lstrip())]
        rest = core[len(prefix_ws):]
        tokens = rest.split()

        if not tokens or tokens[0].upper() != "FROM":
            out.append(line)
            continue

        i = 1
        while i < len(tokens) and tokens[i].startswith("--"):
            i += 1

        if i >= len(tokens):
            out.append(line)
            continue

        image = tokens[i]
        rebuilt = [tokens[0], *tokens[1:i], rewrite(image, registry), *tokens[i + 1:]]
        out.append(prefix_ws + " ".join(rebuilt) + newline)

    return "".join(out)


def temp_file_same_dir(src_path: str, suffix: str):
    src_path = os.path.abspath(src_path)
    d = os.path.dirname(src_path) or "."
    base = os.path.basename(src_path)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{base}.rewritten.",
        suffix=suffix,
        dir=d,
        text=True,
    )
    os.close(fd)
    TEMP_PATHS.append(tmp)
    return tmp


def rewrite_dockerfile(path: str, registry: str = REGISTRY) -> str:
    if not os.path.exists(path):
        return path

    with open(path, "r", encoding="utf-8") as f:
        original = f.read()

    rewritten = rewrite_dockerfile_text(original, registry)
    if rewritten == original:
        return path

    tmp = temp_file_same_dir(path, suffix=".Dockerfile")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(rewritten)

    return tmp


def rewrite_compose_doc(doc, compose_dir: str, registry: str = REGISTRY):
    if isinstance(doc, dict):
        out = {}
        for k, v in doc.items():
            if k == "image" and isinstance(v, str):
                out[k] = rewrite(v, registry)
            elif k == "build":
                if isinstance(v, dict):
                    vv = dict(v)
                    dockerfile = vv.get("dockerfile")
                    context = vv.get("context", ".")

                    if isinstance(dockerfile, str):
                        context_abs = context
                        if not os.path.isabs(context_abs):
                            context_abs = os.path.normpath(os.path.join(compose_dir, context_abs))

                        dockerfile_abs = dockerfile
                        if not os.path.isabs(dockerfile_abs):
                            dockerfile_abs = os.path.normpath(os.path.join(context_abs, dockerfile_abs))

                        if os.path.exists(dockerfile_abs):
                            tmp_df = rewrite_dockerfile(dockerfile_abs, registry)
                            vv["dockerfile"] = tmp_df  # absolute path is safest

                    out[k] = rewrite_compose_doc(vv, compose_dir, registry)
                else:
                    out[k] = rewrite_compose_doc(v, compose_dir, registry)
            else:
                out[k] = rewrite_compose_doc(v, compose_dir, registry)
        return out

    if isinstance(doc, list):
        return [rewrite_compose_doc(x, compose_dir, registry) for x in doc]

    return doc


def rewrite_compose_file(path: str, registry: str = REGISTRY) -> str:
    if yaml is None or not os.path.exists(path):
        return path

    compose_dir = os.path.dirname(os.path.abspath(path)) or "."
    with open(path, "r", encoding="utf-8") as f:
        docs = list(yaml.safe_load_all(f))

    new_docs = [rewrite_compose_doc(d, compose_dir, registry) if d is not None else None for d in docs]

    tmp = temp_file_same_dir(path, suffix=".compose.yml")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump_all(new_docs, f, sort_keys=False)

    return tmp


def strip_file_args(argv):
    out = []
    files = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-f", "--file") and i + 1 < len(argv):
            files.append(argv[i + 1])
            i += 2
            continue
        if a.startswith("--file="):
            files.append(a.split("=", 1)[1])
            i += 1
            continue
        out.append(a)
        i += 1
    return out, files


def compose_default_files():
    return [
        c
        for c in (
            "compose.yaml",
            "compose.yml",
            "docker-compose.yaml",
            "docker-compose.yml",
        )
        if os.path.exists(c)
    ]


def env_with_buildkit_off():
    env = os.environ.copy()
    env["DOCKER_BUILDKIT"] = "0"
    env["COMPOSE_DOCKER_CLI_BUILD"] = "0"
    return env


def run_real(argv, env=None):
    result = subprocess.run([REAL, *argv], env=env)
    sys.exit(result.returncode)


def main():
    argv = sys.argv[1:]
    if not argv:
        os.execv(REAL, [REAL])

    cmd = argv[0]
    rest = argv[1:]

    if cmd in {"pull", "run", "create"}:
        run_real([cmd, *rewrite_first_image(rest)])

    if cmd == "build":
        build_args = list(rest)
        dockerfile = None
        out = []
        i = 0
        while i < len(build_args):
            a = build_args[i]
            if a in ("-f", "--file") and i + 1 < len(build_args):
                dockerfile = build_args[i + 1]
                i += 2
                continue
            if a.startswith("--file="):
                dockerfile = a.split("=", 1)[1]
                i += 1
                continue
            out.append(a)
            i += 1

        if dockerfile is None and os.path.exists("Dockerfile"):
            dockerfile = "Dockerfile"

        if dockerfile is not None:
            run_real(
                [cmd, "-f", rewrite_dockerfile(dockerfile), *out],
                env=env_with_buildkit_off(),
            )

        run_real([cmd, *rest], env=env_with_buildkit_off())

    if cmd == "buildx":
        if not rest:
            run_real(argv)

        sub = rest[0]
        subrest = rest[1:]

        if sub == "build":
            build_args = list(subrest)
            dockerfile = None
            out = []
            i = 0
            while i < len(build_args):
                a = build_args[i]
                if a in ("-f", "--file") and i + 1 < len(build_args):
                    dockerfile = build_args[i + 1]
                    i += 2
                    continue
                if a.startswith("--file="):
                    dockerfile = a.split("=", 1)[1]
                    i += 1
                    continue
                out.append(a)
                i += 1

            if dockerfile is None and os.path.exists("Dockerfile"):
                dockerfile = "Dockerfile"

            if dockerfile is not None:
                run_real(
                    [cmd, sub, "-f", rewrite_dockerfile(dockerfile), *out],
                    env=env_with_buildkit_off(),
                )

            run_real([cmd, sub, *subrest], env=env_with_buildkit_off())

        if sub == "bake":
            files_rest, explicit = strip_file_args(subrest)
            files = explicit or compose_default_files()
            if files:
                temps = [rewrite_compose_file(f) for f in files]
                env = env_with_buildkit_off()
                env["COMPOSE_FILE"] = os.pathsep.join(temps)
                run_real([cmd, sub, *files_rest], env=env)

            run_real([cmd, sub, *subrest], env=env_with_buildkit_off())

    if cmd == "compose":
        rest2, explicit = strip_file_args(rest)
        files = explicit or compose_default_files()
        if files:
            temps = [rewrite_compose_file(f) for f in files]
            env = env_with_buildkit_off()
            env["COMPOSE_FILE"] = os.pathsep.join(temps)
            run_real([cmd, *rest2], env=env)

        run_real([cmd, *rest2], env=env_with_buildkit_off())

    if cmd in {"image", "container"} and rest:
        sub = rest[0]
        subrest = rest[1:]
        if sub in {"pull", "run", "create"}:
            run_real([cmd, sub, *rewrite_first_image(subrest)])
        run_real([cmd, *rest])

    run_real(argv)


if __name__ == "__main__":
    main()

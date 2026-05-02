#!/usr/bin/env python3
"""
Docker CLI wrapper that rewrites image references to use a custom registry.

This script intercepts docker commands and rewrites unqualified image references
to use a custom registry address. It handles various Docker subcommands including:
- pull, run, create, push, rmi, save, tag, commit
- image subcommands (pull, run, create, push, rm, save, tag, commit, build)
- compose and buildx commands

Usage:
    DOCKER_REGISTRY=10.0.2.100:5000 python3 docker.py <docker-args...>

The script expects /usr/bin/docker.real to exist (the actual docker binary).
"""
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

if not os.path.isfile(REAL):
    print(f"Error: docker.real binary not found at '{REAL}'.", file=sys.stderr)
    print("Set DOCKER_REAL environment variable to the correct path.", file=sys.stderr)
    sys.exit(1)

# Boolean flags that don't consume the next argument
_BOOLEAN_FLAGS = {
    "--all", "-a", "--attach",
    "--detach", "-d",
    "--force-pull", "--force-rm",
    "--help", "-h",
    "--interactive", "-i",
    "--no-pull",
    "--privileged",
    "--quiet", "-q",
    "--rm",
    "--tty", "-t", "-T",
    "--version",
}


def cleanup():
    """Clean up temporary files created during image rewriting."""
    for p in TEMP_PATHS:
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass


atexit.register(cleanup)


def is_flag(arg: str) -> bool:
    """Check if argument is a flag (starts with - but not just "-")."""
    return arg.startswith("-") and arg != "-"


def is_qualified(ref: str) -> bool:
    """Check if image reference is already qualified (scratch, localhost, or has dot/port in host)."""
    if ref == "scratch":
        return True
    if "/" not in ref:
        return False
    head = ref.split("/", 1)[0]
    return head == "localhost" or "." in head or ":" in head


def rewrite(ref: str, registry: str = REGISTRY) -> str:
    """Rewrite unqualified image reference to use custom registry."""
    if ref.startswith(registry + "/") or is_qualified(ref):
        return ref
    return f"{registry}/{ref}"


def _skip_flag_args(args, i):
    """
    Skip flag arguments starting at index i.
    
    Returns the next index after all flag arguments have been processed.
    Handles:
    - Flags with embedded values (e.g., --file=/path)
    - Flags with separate values (e.g., -t python:3.11)
    """
    while i < len(args):
        token = args[i]
        
        if is_flag(token):
            # flags with = have their value embedded (e.g. -e=FOO=BAR, --name=mycontainer)
            # so only advance by 1
            if "=" in token:
                i += 1
            # known boolean flags don't consume the next argument
            elif token in _BOOLEAN_FLAGS:
                i += 1
            # skip next token if it's a value (not another flag)
            elif i + 1 < len(args) and not is_flag(args[i + 1]):
                i += 2
            else:
                i += 1
            continue
        
        # Found non-flag, return current index
        break
    
    return i


def rewrite_first_image(args, registry: str = REGISTRY):
    """
    Rewrite the first non-flag image argument.
    
    Handles flags like -t, --name, etc. that consume the next argument.
    For flags with embedded values (e.g., --file=/path), the value is part of the flag.
    """
    out = list(args)
    
    # Skip all leading flags to find first non-flag argument
    i = _skip_flag_args(out, 0)
    
    # If we found a non-flag argument, rewrite it as an image reference
    if i < len(out):
        out[i] = rewrite(out[i], registry)
    
    return out


def rewrite_push_image(args, registry: str = REGISTRY):
    """
    Rewrite the first non-flag image argument, treating --all-tags as boolean.
    
    Handles special case for --all-tags/-a flag which doesn't consume arguments.
    """
    out = list(args)
    
    i = 0
    while i < len(out):
        token = out[i]
        
        if is_flag(token):
            # Special handling for --all-tags/-a flags that don't take values
            if token in ("--all-tags", "-a"):
                i += 1
                continue
            
            # Handle regular flags with embedded values (e.g., --file=/path)
            if "=" in token:
                i += 1
            # Handle flags with separate values (e.g., -t python:3.11)
            elif i + 1 < len(out) and not is_flag(out[i + 1]):
                i += 2
            else:
                i += 1
            continue
        
        # Found first non-flag argument, rewrite it as an image reference
        out[i] = rewrite(out[i], registry)
        return out
    
    return out


def rewrite_tag_args(args, registry: str = REGISTRY):
    """
    Rewrite SOURCE_IMAGE and TARGET_IMAGE for docker tag.
    
    docker tag [OPTIONS] SOURCE_IMAGE[:TAG] TARGET_IMAGE[:TAG]
    Both positional args are image references that need rewriting.
    """
    out = list(args)
    
    # Skip flags to find first positional (SOURCE_IMAGE)
    i = _skip_flag_args(out, 0)
    
    # First positional = SOURCE_IMAGE
    if i < len(out):
        out[i] = rewrite(out[i], registry)
        i += 1
    
    # Skip any remaining flags between source and target
    i = _skip_flag_args(out, i)
    
    # Second positional = TARGET_IMAGE
    if i < len(out):
        out[i] = rewrite(out[i], registry)
    
    return out


def rewrite_commit_args(args, registry: str = REGISTRY):
    """
    Rewrite the optional REPOSITORY[:TAG] for docker commit.
    
    docker commit [OPTIONS] CONTAINER [REPOSITORY[:TAG]]
    The first positional is the container name (not rewritten).
    The second positional (if present) is an image ref (rewritten).
    """
    out = list(args)
    
    # Skip flags to find first positional (CONTAINER)
    i = _skip_flag_args(out, 0)
    
    # First positional = CONTAINER (skip it)
    if i < len(out):
        i += 1
    
    # Skip any remaining flags
    i = _skip_flag_args(out, i)
    
    # Second positional = REPOSITORY[:TAG] (optional, rewrite if present)
    if i < len(out):
        out[i] = rewrite(out[i], registry)
    
    return out


def rewrite_all_images(args, registry: str = REGISTRY):
    """
    Rewrite all non-flag image arguments.
    
    Handles flags like -t, --name, etc. that consume the next argument.
    For flags with embedded values (e.g., --file=/path), the value is part of the flag.
    """
    out = list(args)
    
    i = 0
    while i < len(out):
        # Skip all flags at current position
        i = _skip_flag_args(out, i)
        
        # If we found a non-flag argument, rewrite it as an image reference
        if i < len(out):
            out[i] = rewrite(out[i], registry)
            i += 1
    
    return out


def rewrite_dockerfile_text(text: str, registry: str = REGISTRY) -> str:
    """
    Rewrite FROM lines in Dockerfile text to use custom registry.
    
    Args:
        text (str): The Dockerfile content as a string
        registry (str): The registry to prepend to unqualified images
        
    Returns:
        str: The modified Dockerfile content with FROM lines rewritten
    """
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
    """
    Create a temporary file in the same directory as the source file.
    
    Args:
        src_path (str): Path to the source file
        suffix (str): Suffix to append to the temporary filename
        
    Returns:
        str: Path to the created temporary file
    """
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
    """
    Rewrite FROM lines in a Dockerfile to use custom registry.
    
    Args:
        path (str): Path to the Dockerfile
        registry (str): The registry to prepend to unqualified images
        
    Returns:
        str: Path to the rewritten file (original if no changes needed)
    """
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
    """
    Recursively rewrite image references in compose document.
    
    Args:
        doc: The compose document (dict, list, or other)
        compose_dir (str): Directory containing the compose file
        registry (str): The registry to prepend to unqualified images
        
    Returns:
        The modified compose document with image references rewritten
    """
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
    """
    Rewrite image references in a compose file to use custom registry.
    
    Args:
        path (str): Path to the compose file
        registry (str): The registry to prepend to unqualified images
        
    Returns:
        str: Path to the rewritten file (original if yaml not available or no changes needed)
    """
    if yaml is None or not os.path.exists(path):
        return path

    compose_dir = os.path.dirname(os.path.abspath(path)) or "."
    with open(path, "r", encoding="utf-8") as f:
        docs = list(yaml.safe_load_all(f))

    new_docs = [rewrite_compose_doc(d, compose_dir, registry) if d is not None else None for d in docs]

    original_yaml = yaml.safe_dump_all(docs, sort_keys=False)
    new_yaml = yaml.safe_dump_all(new_docs, sort_keys=False)

    if original_yaml == new_yaml:
        return path

    tmp = temp_file_same_dir(path, suffix=".compose.yml")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump_all(new_docs, f, sort_keys=False)

    return tmp


def _extract_dockerfile(args):
    """
    Extract the Dockerfile path from build args.
    
    Args:
        args (list): Command line arguments
        
    Returns:
        tuple: (dockerfile_path, remaining_args)
    """
    dockerfile = None
    out = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-f", "--file") and i + 1 < len(args):
            dockerfile = args[i + 1]
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
    return dockerfile, out


def _run_build(cmd_list, rest, dockerfile_arg="-f"):
    """
    Run a build command with Dockerfile FROM rewriting.
    
    Args:
        cmd_list (list): List of command tokens, e.g. ["build"] or ["image", "build"]
        rest (list): Remaining arguments
        dockerfile_arg (str): The flag used for specifying Dockerfile (default: "-f")
    """
    build_args = list(rest)
    dockerfile, out = _extract_dockerfile(build_args)

    if dockerfile is not None:
        sys.exit(run_real(
            [*cmd_list, dockerfile_arg, rewrite_dockerfile(dockerfile), *out],
            env=env_with_buildkit_off(),
        ))

    sys.exit(run_real([*cmd_list, *rest], env=env_with_buildkit_off()))


def strip_file_args(argv):
    """
    Strip file arguments from command line.
    
    Args:
        argv (list): Command line arguments
        
    Returns:
        tuple: (remaining_args, file_paths)
    """
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
    """
    Get list of default compose filenames that exist in current directory.
    
    Returns:
        list: List of existing compose filenames
    """
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
    """
    Create environment with BuildKit and Compose CLI build disabled.
    
    Returns:
        dict: Environment dictionary with BUILDKIT flags set to 0
    """
    env = os.environ.copy()
    env["DOCKER_BUILDKIT"] = "0"
    env["COMPOSE_DOCKER_CLI_BUILD"] = "0"
    return env


def run_real(argv, env=None):
    """
    Execute the real docker command with given arguments.
    
    Args:
        argv (list): Arguments to pass to docker
        env (dict): Environment variables to use
        
    Returns:
        int: Return code of the subprocess
    """
    result = subprocess.run([REAL, *argv], env=env)
    return result.returncode


def main():
    """
    Main entry point for the docker wrapper.
    
    Parses command line arguments and routes to appropriate handlers.
    """
    argv = sys.argv[1:]
    if not argv:
        os.execv(REAL, [REAL])

    cmd = argv[0]
    rest = argv[1:]

    # Handle commands that rewrite first image argument
    if cmd in {"pull", "run", "create"}:
        sys.exit(run_real([cmd, *rewrite_first_image(rest)]))

    # Handle push command with special handling for --all-tags flag
    if cmd == "push":
        sys.exit(run_real([cmd, *rewrite_push_image(rest)]))

    # Handle commands that rewrite all image arguments
    if cmd in {"rmi", "save"}:
        sys.exit(run_real([cmd, *rewrite_all_images(rest)]))

    # Handle tag command with two positional image arguments
    if cmd == "tag":
        sys.exit(run_real([cmd, *rewrite_tag_args(rest)]))

    # Handle commit command with optional repository argument
    if cmd == "commit":
        sys.exit(run_real([cmd, *rewrite_commit_args(rest)]))

    # Handle build command
    if cmd == "build":
        _run_build([cmd], rest)

    # Handle buildx commands
    if cmd == "buildx":
        if not rest:
            sys.exit(run_real(argv))

        sub = rest[0]
        subrest = rest[1:]

        if sub == "build":
            _run_build([cmd, sub], subrest)

        if sub == "bake":
            files_rest, explicit = strip_file_args(subrest)
            files = explicit or compose_default_files()
            if files:
                temps = [rewrite_compose_file(f) for f in files]
                env = env_with_buildkit_off()
                env["COMPOSE_FILE"] = os.pathsep.join(temps)
                sys.exit(run_real([cmd, sub, *files_rest], env=env))

            sys.exit(run_real([cmd, sub, *subrest], env=env_with_buildkit_off()))

    # Handle builder commands
    if cmd == "builder":
        if not rest:
            sys.exit(run_real(argv))

        sub = rest[0]
        subrest = rest[1:]

        if sub == "build":
            _run_build([cmd, sub], subrest)

        sys.exit(run_real([cmd, *rest]))

    # Handle compose commands
    if cmd == "compose":
        rest2, explicit = strip_file_args(rest)
        files = explicit or compose_default_files()
        if files:
            temps = [rewrite_compose_file(f) for f in files]
            env = env_with_buildkit_off()
            env["COMPOSE_FILE"] = os.pathsep.join(temps)
            sys.exit(run_real([cmd, *rest2], env=env))

        sys.exit(run_real([cmd, *rest2], env=env_with_buildkit_off()))

   # Handle image and container subcommands
    if cmd in {"image", "container"} and rest:
        sub = rest[0]
        subrest = rest[1:]
        if sub in {"pull", "run", "create"}:
            sys.exit(run_real([cmd, sub, *rewrite_first_image(subrest)]))
        if sub == "rm":
            sys.exit(run_real([cmd, sub, *rewrite_all_images(subrest)]))
        if sub == "push":
            sys.exit(run_real([cmd, sub, *rewrite_push_image(subrest)]))
        if sub == "save":
            sys.exit(run_real([cmd, sub, *rewrite_all_images(subrest)]))
        if sub == "tag":
            sys.exit(run_real([cmd, sub, *rewrite_tag_args(subrest)]))
        if sub == "commit":
            sys.exit(run_real([cmd, sub, *rewrite_commit_args(subrest)]))
        if sub == "build":
            _run_build([cmd, sub], subrest)
        sys.exit(run_real([cmd, *rest]))

    # Default case - pass through unchanged
    sys.exit(run_real(argv))


if __name__ == "__main__":
    main()

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
import copy
import logging
import os
import re
import subprocess
import sys
import tempfile



LOG_LEVEL = os.environ.get("DOCKER_WRAPPER_LOG_LEVEL", "WARNING").upper()
debug_mode = os.environ.get("DOCKER_WRAPPER_DEBUG", "").lower() in ("1", "true", "yes")

if debug_mode:
    effective_level = logging.DEBUG
else:
    effective_level = getattr(logging, LOG_LEVEL, logging.WARNING)

logging.basicConfig(
    level=effective_level,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("docker-wrapper")

TEMP_PATHS = []





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
        logger.debug("is_qualified: %s -> True (scratch)", ref)
        return True
    if "/" not in ref:
        logger.debug("is_qualified: %s -> False (no slash)", ref)
        return False
    head = ref.split("/", 1)[0]
    qualified = head == "localhost" or "." in head or ":" in head
    if qualified:
        logger.debug("is_qualified: %s -> True (head=%s)", ref, head)
    else:
        logger.debug("is_qualified: %s -> False (head=%s)", ref, head)
    return qualified


def rewrite(ref: str, registry: str = None) -> str:
    """Rewrite unqualified image reference to use custom registry."""
    if registry is None:
        registry = REGISTRY
    if ref.startswith(registry + "/") or is_qualified(ref):
        logger.debug("rewrite: %s -> %s (unchanged, already qualified or prefixed)", ref, ref)
        return ref
    result = f"{registry}/{ref}"
    logger.debug("rewrite: %s -> %s", ref, result)
    return result


def _skip_flag_args(args, i):
    """
    Skip flag arguments starting at index i.
    
    Returns the next index after all flag arguments have been processed.
    Handles:
    - Flags with embedded values (e.g., --file=/path)
    - Flags with separate values (e.g., -t python:3.11)
    """
    start_i = i
    while i < len(args):
        token = args[i]
        
        if is_flag(token):
            # flags with = have their value embedded (e.g. -e=FOO=BAR, --name=mycontainer)
            # so only advance by 1
            if "=" in token:
                logger.debug("_skip_flag_args: %s -> flag with =, advance to %d", token, i + 1)
                i += 1
            # known boolean flags don't consume the next argument
            elif token in _BOOLEAN_FLAGS:
                logger.debug("_skip_flag_args: %s -> boolean flag, advance to %d", token, i + 1)
                i += 1
            # skip next token if it's a value (not another flag)
            elif i + 1 < len(args) and not is_flag(args[i + 1]):
                logger.debug("_skip_flag_args: %s -> flag with value %s, advance to %d", token, args[i + 1], i + 2)
                i += 2
            else:
                logger.debug("_skip_flag_args: %s -> unknown flag, advance to %d", token, i + 1)
                i += 1
            continue
        
        # Found non-flag, return current index
        logger.debug("_skip_flag_args: %s -> non-flag at index %d", token, i)
        break
    
    if i != start_i:
        logger.debug("_skip_flag_args: advanced from %d to %d", start_i, i)
    return i


def rewrite_first_image(args, registry: str = None):
    """
    Rewrite the first non-flag image argument.
    
    Handles flags like -t, --name, etc. that consume the next argument.
    For flags with embedded values (e.g., --file=/path), the value is part of the flag.
    """
    if registry is None:
        registry = REGISTRY

    out = list(args)
    logger.debug("rewrite_first_image: args=%s, registry=%s", args, registry)
    
    # Skip all leading flags to find first non-flag argument
    i = _skip_flag_args(out, 0)
    
    # If we found a non-flag argument, rewrite it as an image reference
    if i < len(out):
        out[i] = rewrite(out[i], registry)
    
    return out


def rewrite_push_image(args, registry: str = None):
    """
    Rewrite the first non-flag image argument, treating --all-tags as boolean.
    
    Handles special case for --all-tags/-a flag which doesn't consume arguments.
    """
    if registry is None:
        registry = REGISTRY

    out = list(args)
    logger.debug("rewrite_push_image: args=%s, registry=%s", args, registry)
    
    i = 0
    while i < len(out):
        token = out[i]
        
        if is_flag(token):
            # Special handling for --all-tags/-a flags that don't take values
            if token in ("--all-tags", "-a"):
                logger.debug("rewrite_push_image: %s -> boolean flag (all-tags), skip", token)
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


def rewrite_tag_args(args, registry: str = None):
    """
    Rewrite SOURCE_IMAGE and TARGET_IMAGE for docker tag.
    
    docker tag [OPTIONS] SOURCE_IMAGE[:TAG] TARGET_IMAGE[:TAG]
    Both positional args are image references that need rewriting.
    """
    if registry is None:
        registry = REGISTRY

    out = list(args)
    logger.debug("rewrite_tag_args: args=%s, registry=%s", args, registry)
    
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


def rewrite_commit_args(args, registry: str = None):
    """
    Rewrite the optional REPOSITORY[:TAG] for docker commit.
    
    docker commit [OPTIONS] CONTAINER [REPOSITORY[:TAG]]
    The first positional is the container name (not rewritten).
    The second positional (if present) is an image ref (rewritten).
    """
    if registry is None:
        registry = REGISTRY

    out = list(args)
    logger.debug("rewrite_commit_args: args=%s, registry=%s", args, registry)
    
    # Skip flags to find first positional (CONTAINER)
    i = _skip_flag_args(out, 0)
    
    # First positional = CONTAINER (skip it)
    if i < len(out):
        logger.debug("rewrite_commit_args: skipping container arg at index %d", i)
        i += 1
    
    # Skip any remaining flags
    i = _skip_flag_args(out, i)
    
    # Second positional = REPOSITORY[:TAG] (optional, rewrite if present)
    if i < len(out):
        out[i] = rewrite(out[i], registry)
    
    return out


def rewrite_all_images(args, registry: str = None):
    """
    Rewrite all non-flag image arguments.
    
    Handles flags like -t, --name, etc. that consume the next argument.
    For flags with embedded values (e.g., --file=/path), the value is part of the flag.
    """
    if registry is None:
        registry = REGISTRY

    out = list(args)
    logger.debug("rewrite_all_images: args=%s, registry=%s", args, registry)
    
    i = 0
    while i < len(out):
        # Skip all flags at current position
        i = _skip_flag_args(out, i)
        
        # If we found a non-flag argument, rewrite it as an image reference
        if i < len(out):
            out[i] = rewrite(out[i], registry)
            i += 1
    
    return out


def rewrite_dockerfile_text(text: str, registry: str = None) -> str:
    """
    Rewrite FROM lines in Dockerfile text to use custom registry.
    
    Args:
        text (str): The Dockerfile content as a string
        registry (str): The registry to prepend to unqualified images
        
    Returns:
        str: The modified Dockerfile content with FROM lines rewritten
    """
    if registry is None:
        registry = REGISTRY

    out = []
    from_count = 0

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
        from_count += 1
        rebuilt = [tokens[0], *tokens[1:i], rewrite(image, registry), *tokens[i + 1:]]
        out.append(prefix_ws + " ".join(rebuilt) + newline)

    logger.debug("rewrite_dockerfile_text: processed %d FROM lines", from_count)
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
    logger.debug("temp_file_same_dir: %s -> %s", src_path, tmp)
    return tmp


def rewrite_dockerfile(path: str, registry: str = None):
    """
    Rewrite FROM lines in a Dockerfile to use custom registry.
    
    Args:
        path (str): Path to the Dockerfile
        registry (str): The registry to prepend to unqualified images
        
    Returns:
        str: Path to the rewritten file (original if no changes needed)
    """
    if registry is None:
        registry = REGISTRY

    logger.debug("rewrite_dockerfile: path=%s, registry=%s", path, registry)

    if not os.path.exists(path):
        logger.debug("rewrite_dockerfile: file does not exist: %s", path)
        return path

    with open(path, "r", encoding="utf-8") as f:
        original = f.read()

    rewritten = rewrite_dockerfile_text(original, registry)
    if rewritten == original:
        logger.debug("rewrite_dockerfile: no changes needed, returning original")
        return path

    tmp = temp_file_same_dir(path, suffix=".Dockerfile")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(rewritten)

    return tmp


def rewrite_compose_doc(doc, compose_dir: str, registry: str = None):
    """
    Recursively rewrite image references in compose document.
    
    Args:
        doc: The compose document (dict, list, or other)
        compose_dir (str): Directory containing the compose file
        registry (str): The registry to prepend to unqualified images
        
    Returns:
        The modified compose document with image references rewritten
    """
    if registry is None:
        registry = REGISTRY

    if isinstance(doc, dict):
        out = {}
        for k, v in doc.items():
            if k == "image" and isinstance(v, str):
                out[k] = rewrite(v, registry)
            elif k == "build":
                if isinstance(v, dict):
                    vv = copy.deepcopy(v)
                    dockerfile = vv.get("dockerfile")
                    context = vv.get("context", ".")

                    if isinstance(dockerfile, str):
                        context_abs = context
                        if not os.path.isabs(context_abs):
                            context_abs = os.path.normpath(os.path.join(compose_dir, context_abs))

                        dockerfile_abs = dockerfile
                        if not os.path.isabs(dockerfile_abs):
                            dockerfile_abs = os.path.normpath(os.path.join(context_abs, dockerfile_abs))

                        logger.debug("rewrite_compose_doc: build dockerfile=%s -> %s (exists=%s)",
                                     dockerfile, dockerfile_abs, os.path.exists(dockerfile_abs))
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


def rewrite_compose_text(text: str, compose_dir: str, registry: str = None) -> str:
    """
    Rewrite image references in compose file text to use custom registry.

    Uses line-by-line processing similar to rewrite_dockerfile_text.
    Handles:
    - image: <value> lines
    - dockerfile: <value> lines inside build: blocks

    Args:
        text (str): The compose file content
        compose_dir (str): Directory containing the compose file
        registry (str): The registry to prepend to unqualified images

    Returns:
        str: The modified compose file content
    """
    if registry is None:
        registry = REGISTRY

    out = []
    in_build = False
    build_indent = -1

    for line in text.splitlines(True):
        stripped = line.rstrip()

        # Track build: block entry/exit
        if stripped and not stripped.lstrip().startswith("#"):
            current_indent = len(stripped) - len(stripped.lstrip())
            stripped_content = stripped.lstrip()

            # Check if this line starts a build: block (at same or higher indent than current build)
            if stripped_content.startswith("build:"):
                if not in_build or current_indent <= build_indent:
                    in_build = True
                    build_indent = current_indent

            # Check if we've dedented out of the build block
            if in_build and current_indent < build_indent and not stripped_content.startswith("- "):
                in_build = False
                build_indent = -1

        # Rewrite image: lines
        if re.match(r'^(\s*)image:\s+', line):
            match = re.match(r'^(\s*image:\s+)(.+)', line)
            if match:
                prefix = match.group(1)
                image_ref = match.group(2).strip()
                # Remove quotes if present
                if (image_ref.startswith('"') and image_ref.endswith('"')) or \
                   (image_ref.startswith("'") and image_ref.endswith("'")):
                    image_ref = image_ref[1:-1]
                rewritten = rewrite(image_ref, registry)
                out.append(f"{prefix}{rewritten}\n" if line.endswith("\n") else f"{prefix}{rewritten}")
                continue

        # Rewrite dockerfile: lines inside build: blocks
        if in_build and re.match(r'^(\s*)dockerfile:\s+', line):
            match = re.match(r'^(\s*dockerfile:\s+)(.+)', line)
            if match:
                prefix = match.group(1)
                dockerfile_ref = match.group(2).strip()
                # Remove quotes if present
                if (dockerfile_ref.startswith('"') and dockerfile_ref.endswith('"')) or \
                   (dockerfile_ref.startswith("'") and dockerfile_ref.endswith("'")):
                    dockerfile_ref = dockerfile_ref[1:-1]
                # Resolve path and rewrite
                df_path = dockerfile_ref
                if not os.path.isabs(df_path):
                    df_path = os.path.normpath(os.path.join(compose_dir, df_path))
                if os.path.exists(df_path):
                    tmp_df = rewrite_dockerfile(df_path, registry)
                    out.append(f"{prefix}{tmp_df}\n" if line.endswith("\n") else f"{prefix}{tmp_df}")
                    continue

        out.append(line)

    return "".join(out)


def rewrite_compose_file(path: str, registry: str = None):
    """
    Rewrite image references in a compose file to use custom registry.

    Args:
        path (str): Path to the compose file
        registry (str): The registry to prepend to unqualified images

    Returns:
        str: Path to the rewritten file (original if no changes needed)
    """
    if registry is None:
        registry = REGISTRY

    logger.debug("rewrite_compose_file: path=%s, registry=%s", path, registry)

    if not os.path.exists(path):
        logger.debug("rewrite_compose_file: path does not exist: %s", path)
        return path

    compose_dir = os.path.dirname(os.path.abspath(path)) or "."
    with open(path, "r", encoding="utf-8") as f:
        original = f.read()

    rewritten = rewrite_compose_text(original, compose_dir, registry)
    if rewritten == original:
        logger.debug("rewrite_compose_file: no changes detected, returning original")
        return path

    tmp = temp_file_same_dir(path, suffix=".compose.yml")
    logger.debug("rewrite_compose_file: created temp file: %s", tmp)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(rewritten)

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
            logger.debug("_extract_dockerfile: found -f/--file=%s", dockerfile)
            i += 2
            continue
        if a.startswith("--file="):
            dockerfile = a.split("=", 1)[1]
            logger.debug("_extract_dockerfile: found --file=%s", dockerfile)
            i += 1
            continue
        out.append(a)
        i += 1
    if dockerfile is None and os.path.exists("Dockerfile"):
        dockerfile = "Dockerfile"
        logger.debug("_extract_dockerfile: default Dockerfile found in CWD")
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
        rewritten_df = rewrite_dockerfile(dockerfile)
        logger.debug("_run_build: cmd_list=%s, dockerfile=%s -> %s, remaining=%s",
                     cmd_list, dockerfile, rewritten_df, out)
        sys.exit(run_real(
            [*cmd_list, dockerfile_arg, rewritten_df, *out],
            env=env_with_buildkit_off(),
        ))

    logger.debug("_run_build: cmd_list=%s, no dockerfile found, using rest=%s", cmd_list, rest)
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
            logger.debug("strip_file_args: extracted file=%s", argv[i + 1])
            i += 2
            continue
        if a.startswith("--file="):
            files.append(a.split("=", 1)[1])
            logger.debug("strip_file_args: extracted file=%s", a.split("=", 1)[1])
            i += 1
            continue
        out.append(a)
        i += 1
    return out, files


def compose_default_files():
    """
    Get list of compose files to use.
    
    Checks COMPOSE_FILE env var first, then falls back to default
    compose filenames in the current directory.
    
    Returns:
        list: List of existing compose file paths
    """
    compose_file = os.environ.get("COMPOSE_FILE")
    if compose_file:
        paths = []
        for f in compose_file.split(os.pathsep):
            f = f.strip()
            if f and os.path.exists(f):
                paths.append(f)
        logger.debug("compose_default_files: COMPOSE_FILE=%s -> found=%s", compose_file, paths)
        return paths

    found = [
        c
        for c in (
            "compose.yaml",
            "compose.yml",
            "docker-compose.yaml",
            "docker-compose.yml",
        )
        if os.path.exists(c)
    ]
    logger.debug("compose_default_files: default search -> found=%s", found)
    return found


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


def run_real(argv, env=None, timeout=None):
    """
    Execute the real docker command with given arguments.
    
    Args:
        argv (list): Arguments to pass to docker
        env (dict): Environment variables to use
        timeout (int): Maximum time in seconds to wait for the command (default: 300)
        
    Returns:
        int: Return code of the subprocess
    """
    if timeout is None:
        timeout = DEFAULT_TIMEOUT
    logger.debug("run_real: REAL=%s, argv=%s, timeout=%s", REAL, argv, timeout)
    try:
        result = subprocess.run([REAL, *argv], env=env, timeout=timeout)
        logger.debug("run_real: returned %s", result.returncode)
        return result.returncode
    except subprocess.TimeoutExpired:
        logger.error("docker command timed out after %ss", timeout)
        return 1


def main():
    """
    Main entry point for the docker wrapper.
    
    Parses command line arguments and routes to appropriate handlers.
    """
    global REAL, REGISTRY, DEFAULT_TIMEOUT
    REAL = os.environ.get("DOCKER_REAL", "/usr/bin/docker.real")
    REGISTRY = os.environ.get("DOCKER_REGISTRY")
    DEFAULT_TIMEOUT = int(os.environ.get("DOCKER_TIMEOUT", "300"))

    if not REGISTRY:
        logger.error("DOCKER_REGISTRY environment variable is not set.")
        logger.error("Set it to your local registry address, e.g.:")
        logger.error("  export DOCKER_REGISTRY=10.0.2.100:5000")
        sys.exit(1)
    if not os.path.isfile(REAL):
        logger.error("docker.real binary not found at '%s'.", REAL)
        logger.error("Set DOCKER_REAL environment variable to the correct path.")
        sys.exit(1)

    argv = sys.argv[1:]
    logger.debug("main: argv=%s", argv)

    if not argv:
        logger.debug("main: no args, execing docker.real directly")
        os.execv(REAL, [REAL])

    cmd = argv[0]
    rest = argv[1:]
    logger.debug("main: cmd=%s, rest=%s", cmd, rest)

    # Handle commands that rewrite first image argument
    if cmd in {"pull", "run", "create"}:
        logger.debug("main: routing to %s with first-image rewrite", cmd)
        sys.exit(run_real([cmd, *rewrite_first_image(rest)]))

    # Handle push command with special handling for --all-tags flag
    if cmd == "push":
        logger.debug("main: routing to push with all-tags handling")
        sys.exit(run_real([cmd, *rewrite_push_image(rest)]))

    # Handle commands that rewrite all image arguments
    if cmd in {"rmi", "save"}:
        logger.debug("main: routing to %s with all-images rewrite", cmd)
        sys.exit(run_real([cmd, *rewrite_all_images(rest)]))

    # Handle tag command with two positional image arguments
    if cmd == "tag":
        logger.debug("main: routing to tag")
        sys.exit(run_real([cmd, *rewrite_tag_args(rest)]))

    # Handle commit command with optional repository argument
    if cmd == "commit":
        logger.debug("main: routing to commit")
        sys.exit(run_real([cmd, *rewrite_commit_args(rest)]))

    # Handle build command
    if cmd == "build":
        logger.debug("main: routing to build")
        _run_build([cmd], rest)

    # Handle buildx commands
    if cmd == "buildx":
        if not rest:
            logger.debug("main: buildx with no subcommand, pass through")
            sys.exit(run_real(argv))

        sub = rest[0]
        subrest = rest[1:]
        logger.debug("main: buildx subcmd=%s", sub)

        if sub == "build":
            _run_build([cmd, sub], subrest)

        if sub == "bake":
            files_rest, explicit = strip_file_args(subrest)
            files = explicit or compose_default_files()
            if files:
                logger.debug("main: buildx bake: compose files=%s", files)
                temps = [rewrite_compose_file(f) for f in files]
                env = env_with_buildkit_off()
                env["COMPOSE_FILE"] = os.pathsep.join(temps)
                sys.exit(run_real([cmd, sub, *files_rest], env=env))

            sys.exit(run_real([cmd, sub, *subrest], env=env_with_buildkit_off()))

    # Handle builder commands
    if cmd == "builder":
        if not rest:
            logger.debug("main: builder with no subcommand, pass through")
            sys.exit(run_real(argv))

        sub = rest[0]
        subrest = rest[1:]
        logger.debug("main: builder subcmd=%s", sub)

        if sub == "build":
            _run_build([cmd, sub], subrest)

        sys.exit(run_real([cmd, *rest]))

    # Handle compose commands
    if cmd == "compose":
        rest2, explicit = strip_file_args(rest)
        files = explicit or compose_default_files()
        if files:
            logger.debug("main: compose: files=%s", files)
            temps = [rewrite_compose_file(f) for f in files]
            env = env_with_buildkit_off()
            env["COMPOSE_FILE"] = os.pathsep.join(temps)
            sys.exit(run_real([cmd, *rest2], env=env))

        sys.exit(run_real([cmd, *rest2], env=env_with_buildkit_off()))

    # Handle image and container subcommands
    if cmd in {"image", "container"} and rest:
        sub = rest[0]
        subrest = rest[1:]
        logger.debug("main: %s subcmd=%s", cmd, sub)
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
    logger.debug("main: default pass-through, no rewriting")
    sys.exit(run_real(argv))


if __name__ == "__main__":
    main()

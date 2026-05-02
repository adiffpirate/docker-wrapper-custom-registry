#!/usr/bin/env python3
"""Functional tests for docker.py wrapper.

These tests run the actual docker.py script as a subprocess against a mock
docker.real binary and verify that image references are rewritten correctly.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT_PATH = os.path.join(SCRIPT_DIR, "docker.py")
REGISTRY = "10.0.2.100:5000"


def run_wrapper(args, registry=REGISTRY, real_path=None, cwd=None, extra_env=None):
    """Run docker.py wrapper and return (stdout, stderr, returncode).

    Pass registry=None to omit DOCKER_REGISTRY entirely (for error tests).
    """
    env = os.environ.copy()
    if registry is not None:
        env["DOCKER_REGISTRY"] = registry
    elif "DOCKER_REGISTRY" in env:
        # Remove it if explicitly set to None to simulate missing variable
        del env["DOCKER_REGISTRY"]
    if real_path:
        env["DOCKER_REAL"] = real_path
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [sys.executable, SCRIPT_PATH] + args,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )
    return result.stdout, result.stderr, result.returncode


def parse_docker_cmd(stdout):
    """Parse 'DOCKER_CMD: <args>' from mock output. Returns list of args."""
    for line in stdout.strip().splitlines():
        if line.startswith("DOCKER_CMD: "):
            return line[len("DOCKER_CMD: "):].split()
    return []


class MockDockerReal:
    """Context manager that provides a temporary mock docker.real binary."""

    def __init__(self):
        self.tmpdir = None
        self.real_path = None

    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp()
        self.real_path = os.path.join(self.tmpdir, "docker.real")
        mock_script = '#!/bin/sh\necho "DOCKER_CMD: $*"\necho "ENV: DOCKER_BUILDKIT=${DOCKER_BUILDKIT:-not set}"\necho "ENV: COMPOSE_DOCKER_CLI_BUILD=${COMPOSE_DOCKER_CLI_BUILD:-not set}"\nexit 0\n'
        with open(self.real_path, "w") as f:
            f.write(mock_script)
        os.chmod(self.real_path, 0o755)
        return self.real_path

    def __exit__(self, *args):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestPull(unittest.TestCase):
    def test_unqualified_image(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(["pull", "python:3.11"], real_path=real_path)
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["pull", "10.0.2.100:5000/python:3.11"])

    def test_qualified_localhost(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(["pull", "localhost/foo:latest"], real_path=real_path)
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["pull", "localhost/foo:latest"])

    def test_qualified_registry(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(["pull", "my.registry.io/bar:1.0"], real_path=real_path)
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["pull", "my.registry.io/bar:1.0"])

    def test_scratch(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(["pull", "scratch"], real_path=real_path)
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["pull", "scratch"])

    def test_already_prefixed(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["pull", "10.0.2.100:5000/python:3.11"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["pull", "10.0.2.100:5000/python:3.11"])


class TestRun(unittest.TestCase):
    def test_unqualified_image(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(["run", "python:3.11"], real_path=real_path)
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["run", "10.0.2.100:5000/python:3.11"])

    def test_with_flag_short(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(["run", "-t", "python:3.11"], real_path=real_path)
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            # -t is a boolean flag, so python:3.11 gets rewritten
            self.assertEqual(args, ["run", "-t", "10.0.2.100:5000/python:3.11"])

    def test_with_flag_long(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["run", "--name", "mycontainer", "python:3.11"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["run", "--name", "mycontainer", "10.0.2.100:5000/python:3.11"])

    def test_with_flag_equals(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["run", "--platform=linux/amd64", "python:3.11"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["run", "--platform=linux/amd64", "10.0.2.100:5000/python:3.11"])

    def test_qualified_image(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["run", "localhost/myapp:latest"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["run", "localhost/myapp:latest"])

    def test_with_volume_and_command(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["run", "-v", "/host:/container", "python:3.11", "echo", "hello"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            # First non-flag image is rewritten; remaining args untouched
            self.assertEqual(args, ["run", "-v", "/host:/container", "10.0.2.100:5000/python:3.11", "echo", "hello"])

    def test_short_flag_with_equals(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["run", "-e=FOO=BAR", "python:3.11"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["run", "-e=FOO=BAR", "10.0.2.100:5000/python:3.11"])

    def test_long_flag_with_equals(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["run", "--env=FOO=BAR", "python:3.11"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["run", "--env=FOO=BAR", "10.0.2.100:5000/python:3.11"])

    def test_multiple_flags_with_equals(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["run", "-e=FOO=BAR", "-e=BAZ=QUX", "python:3.11"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["run", "-e=FOO=BAR", "-e=BAZ=QUX", "10.0.2.100:5000/python:3.11"])

    def test_mixed_flags_with_and_without_equals(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["run", "-e=FOO=BAR", "--name", "mycontainer", "python:3.11"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["run", "-e=FOO=BAR", "--name", "mycontainer", "10.0.2.100:5000/python:3.11"])

    def test_short_flag_without_equals_then_equals_flag(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["run", "-t", "-e=FOO=BAR", "python:3.11"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["run", "-t", "-e=FOO=BAR", "10.0.2.100:5000/python:3.11"])


class TestCreate(unittest.TestCase):
    def test_unqualified_image(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(["create", "ubuntu:22.04"], real_path=real_path)
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["create", "10.0.2.100:5000/ubuntu:22.04"])

    def test_qualified_image(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["create", "registry.example.com/app:1.0"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["create", "registry.example.com/app:1.0"])


class TestRmi(unittest.TestCase):
    def test_unqualified_image(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(["rmi", "python:3.11"], real_path=real_path)
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["rmi", "10.0.2.100:5000/python:3.11"])

    def test_multiple_images(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["rmi", "python:3.11", "alpine:3.18"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["rmi", "10.0.2.100:5000/python:3.11", "10.0.2.100:5000/alpine:3.18"])

    def test_qualified_image_unchanged(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["rmi", "localhost/foo:latest"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["rmi", "localhost/foo:latest"])

    def test_already_prefixed(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["rmi", "10.0.2.100:5000/python:3.11"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["rmi", "10.0.2.100:5000/python:3.11"])


class TestImageRm(unittest.TestCase):
    def test_unqualified_image(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(["image", "rm", "python:3.11"], real_path=real_path)
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["image", "rm", "10.0.2.100:5000/python:3.11"])

    def test_multiple_images(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["image", "rm", "python:3.11", "alpine:3.18"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["image", "rm", "10.0.2.100:5000/python:3.11", "10.0.2.100:5000/alpine:3.18"])


class TestPush(unittest.TestCase):
    def test_unqualified_image(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(["push", "python:3.11"], real_path=real_path)
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["push", "10.0.2.100:5000/python:3.11"])

    def test_qualified_image_unchanged(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["push", "localhost/foo:latest"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["push", "localhost/foo:latest"])

    def test_with_flag(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["push", "--all-tags", "python:3.11"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["push", "--all-tags", "10.0.2.100:5000/python:3.11"])


class TestImagePush(unittest.TestCase):
    def test_unqualified_image(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(["image", "push", "python:3.11"], real_path=real_path)
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["image", "push", "10.0.2.100:5000/python:3.11"])


class TestBuild(unittest.TestCase):
    def test_build_with_dockerfile_flag(self):
        with MockDockerReal() as real_path:
            with tempfile.TemporaryDirectory() as tmpdir:
                dockerfile = os.path.join(tmpdir, "Dockerfile")
                with open(dockerfile, "w") as f:
                    f.write("FROM python:3.11\nRUN echo hello\n")
                stdout, stderr, rc = run_wrapper(
                    ["build", "-f", dockerfile, "."], real_path=real_path, cwd=tmpdir
                )
                self.assertEqual(rc, 0)
                # The wrapper calls run_real twice for build:
                # 1) with rewritten dockerfile path
                # 2) with original args
                lines = stdout.strip().splitlines()
                # First line should have the rewritten dockerfile path
                first_args = parse_docker_cmd(lines[0])
                self.assertEqual(first_args[0], "build")
                self.assertEqual(first_args[1], "-f")
                # second arg is temp file path, should contain .rewritten.
                self.assertIn(".rewritten.", first_args[2])
                self.assertTrue(first_args[2].endswith(".Dockerfile"))

    def test_build_with_qualified_from(self):
        with MockDockerReal() as real_path:
            with tempfile.TemporaryDirectory() as tmpdir:
                dockerfile = os.path.join(tmpdir, "Dockerfile")
                with open(dockerfile, "w") as f:
                    f.write("FROM scratch\nRUN echo\n")
                stdout, stderr, rc = run_wrapper(
                    ["build", "-f", dockerfile, "."], real_path=real_path, cwd=tmpdir
                )
                self.assertEqual(rc, 0)
                # No rewrite needed, so original dockerfile path is used
                lines = stdout.strip().splitlines()
                first_args = parse_docker_cmd(lines[0])
                self.assertEqual(first_args[1], "-f")
                self.assertEqual(first_args[2], dockerfile)


class TestBuildxBuild(unittest.TestCase):
    def test_buildx_build_with_dockerfile_flag(self):
        with MockDockerReal() as real_path:
            with tempfile.TemporaryDirectory() as tmpdir:
                dockerfile = os.path.join(tmpdir, "Dockerfile")
                with open(dockerfile, "w") as f:
                    f.write("FROM python:3.11\nRUN echo\n")
                stdout, stderr, rc = run_wrapper(
                    ["buildx", "build", "-f", dockerfile, "."], real_path=real_path, cwd=tmpdir
                )
                self.assertEqual(rc, 0)
                lines = stdout.strip().splitlines()
                first_args = parse_docker_cmd(lines[0])
                self.assertEqual(first_args[0], "buildx")
                self.assertEqual(first_args[1], "build")
                self.assertEqual(first_args[2], "-f")
                self.assertIn(".rewritten.", first_args[3])
                self.assertTrue(first_args[3].endswith(".Dockerfile"))


class TestImageSubcommand(unittest.TestCase):
    def test_image_pull_unqualified(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["image", "pull", "alpine:3.18"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["image", "pull", "10.0.2.100:5000/alpine:3.18"])

    def test_image_pull_qualified(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["image", "pull", "localhost/foo"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["image", "pull", "localhost/foo"])


class TestMissingEnv(unittest.TestCase):
    def test_missing_docker_registry(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["pull", "python:3.11"],
                registry=None,
                real_path=real_path,
            )
            self.assertNotEqual(rc, 0)
            self.assertIn("DOCKER_REGISTRY", stderr)
            self.assertIn("not set", stderr)

    def test_missing_docker_real(self):
        stdout, stderr, rc = run_wrapper(
            ["pull", "python:3.11"],
            real_path="/nonexistent/path/to/docker.real",
        )
        self.assertNotEqual(rc, 0)
        self.assertIn("docker.real", stderr)
        self.assertIn("not found", stderr)

    def test_timeout_handling(self):
        tmpdir = tempfile.mkdtemp()
        try:
            # Create a Python-based mock that sleeps longer than the test timeout
            slow_real = os.path.join(tmpdir, "docker.real")
            with open(slow_real, "w") as f:
                f.write("#!/usr/bin/env python3\nimport time; time.sleep(9999)\n")
            os.chmod(slow_real, 0o755)

            stdout, stderr, rc = run_wrapper(
                ["pull", "python:3.11"],
                real_path=slow_real,
                extra_env={"DOCKER_TIMEOUT": "1"},
            )
            # Should exit with non-zero due to timeout
            self.assertNotEqual(rc, 0)
            self.assertIn("timed out", stderr)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestBuildKitDisabled(unittest.TestCase):
    def test_build_has_buildkit_off(self):
        with MockDockerReal() as real_path:
            with tempfile.TemporaryDirectory() as tmpdir:
                dockerfile = os.path.join(tmpdir, "Dockerfile")
                with open(dockerfile, "w") as f:
                    f.write("FROM python:3.11\n")
                env = os.environ.copy()
                env["DOCKER_REGISTRY"] = REGISTRY
                env["DOCKER_REAL"] = real_path
                result = subprocess.run(
                    [sys.executable, SCRIPT_PATH, "build", "-f", dockerfile, "."],
                    capture_output=True,
                    text=True,
                    env=env,
                    cwd=tmpdir,
                )
                self.assertEqual(result.returncode, 0)
                # Check that DOCKER_BUILDKIT=0 was passed to the mock
                self.assertIn("DOCKER_BUILDKIT=0", result.stdout)
                self.assertIn("COMPOSE_DOCKER_CLI_BUILD=0", result.stdout)


class TestSave(unittest.TestCase):
    def test_unqualified_image(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(["save", "python:3.11"], real_path=real_path)
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["save", "10.0.2.100:5000/python:3.11"])

    def test_multiple_images(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["save", "python:3.11", "alpine:3.18"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["save", "10.0.2.100:5000/python:3.11", "10.0.2.100:5000/alpine:3.18"])

    def test_with_output_flag(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["save", "-o", "out.tar", "python:3.11"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["save", "-o", "out.tar", "10.0.2.100:5000/python:3.11"])

    def test_with_output_equals_flag(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["save", "--output=out.tar", "python:3.11"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["save", "--output=out.tar", "10.0.2.100:5000/python:3.11"])


class TestImageSave(unittest.TestCase):
    def test_unqualified_image(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(["image", "save", "python:3.11"], real_path=real_path)
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["image", "save", "10.0.2.100:5000/python:3.11"])

    def test_multiple_images(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["image", "save", "python:3.11", "alpine:3.18"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["image", "save", "10.0.2.100:5000/python:3.11", "10.0.2.100:5000/alpine:3.18"])


class TestTag(unittest.TestCase):
    def test_simple(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["tag", "python:3.11", "myrepo/python:3.11"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["tag", "10.0.2.100:5000/python:3.11", "10.0.2.100:5000/myrepo/python:3.11"])

    def test_qualified_source(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["tag", "localhost/foo:latest", "myrepo/foo:latest"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["tag", "localhost/foo:latest", "10.0.2.100:5000/myrepo/foo:latest"])

    def test_qualified_target(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["tag", "python:3.11", "localhost/myrepo:latest"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["tag", "10.0.2.100:5000/python:3.11", "localhost/myrepo:latest"])


class TestImageTag(unittest.TestCase):
    def test_simple(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["image", "tag", "python:3.11", "myrepo/python:3.11"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["image", "tag", "10.0.2.100:5000/python:3.11", "10.0.2.100:5000/myrepo/python:3.11"])


class TestCommit(unittest.TestCase):
    def test_with_repository(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["commit", "mycontainer", "myrepo/myimage:latest"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["commit", "mycontainer", "10.0.2.100:5000/myrepo/myimage:latest"])

    def test_without_repository(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["commit", "mycontainer"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["commit", "mycontainer"])

    def test_with_flag_and_repository(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["commit", "-m", "message", "mycontainer", "myrepo/myimage:latest"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["commit", "-m", "message", "mycontainer", "10.0.2.100:5000/myrepo/myimage:latest"])


class TestContainerCommit(unittest.TestCase):
    def test_with_repository(self):
        with MockDockerReal() as real_path:
            stdout, stderr, rc = run_wrapper(
                ["container", "commit", "mycontainer", "myrepo/myimage:latest"], real_path=real_path
            )
            self.assertEqual(rc, 0)
            args = parse_docker_cmd(stdout)
            self.assertEqual(args, ["container", "commit", "mycontainer", "10.0.2.100:5000/myrepo/myimage:latest"])


class TestImageBuild(unittest.TestCase):
    def test_build_with_dockerfile_flag(self):
        with MockDockerReal() as real_path:
            with tempfile.TemporaryDirectory() as tmpdir:
                dockerfile = os.path.join(tmpdir, "Dockerfile")
                with open(dockerfile, "w") as f:
                    f.write("FROM python:3.11\nRUN echo hello\n")
                stdout, stderr, rc = run_wrapper(
                    ["image", "build", "-f", dockerfile, "."], real_path=real_path, cwd=tmpdir
                )
                self.assertEqual(rc, 0)
                lines = stdout.strip().splitlines()
                first_args = parse_docker_cmd(lines[0])
                self.assertEqual(first_args[0], "image")
                self.assertEqual(first_args[1], "build")
                self.assertEqual(first_args[2], "-f")
                self.assertIn(".rewritten.", first_args[3])
                self.assertTrue(first_args[3].endswith(".Dockerfile"))


class TestBuilderBuild(unittest.TestCase):
    def test_builder_build_with_dockerfile_flag(self):
        with MockDockerReal() as real_path:
            with tempfile.TemporaryDirectory() as tmpdir:
                dockerfile = os.path.join(tmpdir, "Dockerfile")
                with open(dockerfile, "w") as f:
                    f.write("FROM python:3.11\nRUN echo hello\n")
                stdout, stderr, rc = run_wrapper(
                    ["builder", "build", "-f", dockerfile, "."], real_path=real_path, cwd=tmpdir
                )
                self.assertEqual(rc, 0)
                lines = stdout.strip().splitlines()
                first_args = parse_docker_cmd(lines[0])
                self.assertEqual(first_args[0], "builder")
                self.assertEqual(first_args[1], "build")
                self.assertEqual(first_args[2], "-f")
                self.assertIn(".rewritten.", first_args[3])
                self.assertTrue(first_args[3].endswith(".Dockerfile"))


if __name__ == "__main__":
    unittest.main()

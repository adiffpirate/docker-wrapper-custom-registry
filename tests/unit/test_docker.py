#!/usr/bin/env python3
import logging
import os
import sys
import tempfile
import unittest
from unittest import mock

# Ensure the module can be imported without DOCKER_REGISTRY being set
# by mocking it before importing docker
os.environ["DOCKER_REGISTRY"] = "10.0.2.100:5000"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import docker


class TestIsFlag(unittest.TestCase):
    def test_flag_with_dash(self):
        self.assertTrue(docker.is_flag("-f"))

    def test_flag_long(self):
        self.assertTrue(docker.is_flag("--file"))

    def test_flag_with_equals(self):
        self.assertTrue(docker.is_flag("--file=/path"))

    def test_not_flag_plain(self):
        self.assertFalse(docker.is_flag("python:3.11"))

    def test_not_flag_single_dash(self):
        self.assertFalse(docker.is_flag("-"))


class TestIsQualified(unittest.TestCase):
    def test_scratch(self):
        self.assertTrue(docker.is_qualified("scratch"))

    def test_localhost(self):
        self.assertTrue(docker.is_qualified("localhost/foo"))

    def test_dot_in_host(self):
        self.assertTrue(docker.is_qualified("my.registry.io/bar"))

    def test_port_in_host(self):
        self.assertTrue(docker.is_qualified("localhost:5000/foo"))

    def test_unqualified(self):
        self.assertFalse(docker.is_qualified("python:3.11"))

    def test_unqualified_no_slash(self):
        self.assertFalse(docker.is_qualified("ubuntu"))

    def test_unqualified_with_tag(self):
        self.assertFalse(docker.is_qualified("nginx:latest"))


class TestRewrite(unittest.TestCase):
    def test_unqualified(self):
        result = docker.rewrite("python:3.11", registry="10.0.2.100:5000")
        self.assertEqual(result, "10.0.2.100:5000/python:3.11")

    def test_already_prefixed(self):
        result = docker.rewrite("10.0.2.100:5000/python:3.11", registry="10.0.2.100:5000")
        self.assertEqual(result, "10.0.2.100:5000/python:3.11")

    def test_qualified_localhost(self):
        result = docker.rewrite("localhost/foo", registry="10.0.2.100:5000")
        self.assertEqual(result, "localhost/foo")

    def test_qualified_dot_host(self):
        result = docker.rewrite("my.registry.io/bar", registry="10.0.2.100:5000")
        self.assertEqual(result, "my.registry.io/bar")

    def test_qualified_port_host(self):
        result = docker.rewrite("localhost:5000/foo", registry="10.0.2.100:5000")
        self.assertEqual(result, "localhost:5000/foo")

    def test_scratch(self):
        result = docker.rewrite("scratch", registry="10.0.2.100:5000")
        self.assertEqual(result, "scratch")

    def test_different_registry(self):
        result = docker.rewrite("python:3.11", registry="registry.example.com:5000")
        self.assertEqual(result, "registry.example.com:5000/python:3.11")

    def test_unqualified_no_tag(self):
        result = docker.rewrite("ubuntu", registry="10.0.2.100:5000")
        self.assertEqual(result, "10.0.2.100:5000/ubuntu")


class TestRewriteAllImages(unittest.TestCase):
    def test_plain(self):
        result = docker.rewrite_all_images(["python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["10.0.2.100:5000/python:3.11"])

    def test_multiple_images(self):
        result = docker.rewrite_all_images(["python:3.11", "alpine:3.18"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["10.0.2.100:5000/python:3.11", "10.0.2.100:5000/alpine:3.18"])

    def test_with_flag_short(self):
        # -t is a boolean flag, so python:3.11 gets rewritten
        result = docker.rewrite_all_images(["-t", "python:3.11", "alpine:3.18"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["-t", "10.0.2.100:5000/python:3.11", "10.0.2.100:5000/alpine:3.18"])

    def test_with_flag_equals(self):
        result = docker.rewrite_all_images(["--output=out.tar", "python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["--output=out.tar", "10.0.2.100:5000/python:3.11"])

    def test_qualified_unchanged(self):
        result = docker.rewrite_all_images(["localhost/foo", "python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["localhost/foo", "10.0.2.100:5000/python:3.11"])


class TestRewriteFirstImage(unittest.TestCase):
    def test_plain(self):
        result = docker.rewrite_first_image(["python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["10.0.2.100:5000/python:3.11"])

    def test_with_flag_prefix(self):
        # -t is a boolean flag, so python:3.11 gets rewritten
        result = docker.rewrite_first_image(["-t", "python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["-t", "10.0.2.100:5000/python:3.11"])

    def test_with_long_flag_prefix(self):
        result = docker.rewrite_first_image(["--name", "mycontainer", "python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["--name", "mycontainer", "10.0.2.100:5000/python:3.11"])

    def test_flag_with_equals_value(self):
        # --platform=linux/amd64 has = so its value is embedded; next token is the image
        result = docker.rewrite_first_image(["--platform=linux/amd64", "python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["--platform=linux/amd64", "10.0.2.100:5000/python:3.11"])

    def test_already_qualified(self):
        result = docker.rewrite_first_image(["localhost/foo"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["localhost/foo"])

    def test_no_args(self):
        result = docker.rewrite_first_image([], registry="10.0.2.100:5000")
        self.assertEqual(result, [])

    def test_short_flag_with_equals(self):
        result = docker.rewrite_first_image(["-e=FOO=BAR", "python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["-e=FOO=BAR", "10.0.2.100:5000/python:3.11"])

    def test_long_flag_with_equals(self):
        result = docker.rewrite_first_image(["--env=FOO=BAR", "python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["--env=FOO=BAR", "10.0.2.100:5000/python:3.11"])

    def test_multiple_short_flags_with_equals(self):
        result = docker.rewrite_first_image(
            ["-e=FOO=BAR", "-e=BAZ=QUX", "python:3.11"], registry="10.0.2.100:5000"
        )
        self.assertEqual(result, ["-e=FOO=BAR", "-e=BAZ=QUX", "10.0.2.100:5000/python:3.11"])

    def test_multiple_long_flags_with_equals(self):
        result = docker.rewrite_first_image(
            ["--env=FOO=BAR", "--name=mycontainer", "python:3.11"], registry="10.0.2.100:5000"
        )
        self.assertEqual(result, ["--env=FOO=BAR", "--name=mycontainer", "10.0.2.100:5000/python:3.11"])

    def test_mixed_flags_with_and_without_equals(self):
        result = docker.rewrite_first_image(
            ["-e=FOO=BAR", "--name", "mycontainer", "python:3.11"], registry="10.0.2.100:5000"
        )
        self.assertEqual(result, ["-e=FOO=BAR", "--name", "mycontainer", "10.0.2.100:5000/python:3.11"])

    def test_short_flag_without_equals_then_equals_flag(self):
        result = docker.rewrite_first_image(
            ["-t", "-e=FOO=BAR", "python:3.11"], registry="10.0.2.100:5000"
        )
        self.assertEqual(result, ["-t", "-e=FOO=BAR", "10.0.2.100:5000/python:3.11"])


class TestRewriteDockerfileText(unittest.TestCase):
    def test_simple_from(self):
        text = "FROM python:3.11\nRUN echo hello\n"
        result = docker.rewrite_dockerfile_text(text, registry="10.0.2.100:5000")
        self.assertEqual(result, "FROM 10.0.2.100:5000/python:3.11\nRUN echo hello\n")

    def test_from_with_as(self):
        text = "FROM python:3.11 AS builder\n"
        result = docker.rewrite_dockerfile_text(text, registry="10.0.2.100:5000")
        self.assertEqual(result, "FROM 10.0.2.100:5000/python:3.11 AS builder\n")

    def test_from_with_platform(self):
        text = "FROM --platform=linux/amd64 python:3.11\n"
        result = docker.rewrite_dockerfile_text(text, registry="10.0.2.100:5000")
        self.assertEqual(result, "FROM --platform=linux/amd64 10.0.2.100:5000/python:3.11\n")

    def test_from_with_platform_and_as(self):
        text = "FROM --platform=linux/amd64 python:3.11 AS builder\n"
        result = docker.rewrite_dockerfile_text(text, registry="10.0.2.100:5000")
        self.assertEqual(result, "FROM --platform=linux/amd64 10.0.2.100:5000/python:3.11 AS builder\n")

    def test_from_qualified_unchanged(self):
        text = "FROM localhost/foo:latest\n"
        result = docker.rewrite_dockerfile_text(text, registry="10.0.2.100:5000")
        self.assertEqual(result, "FROM localhost/foo:latest\n")

    def test_from_scratch_unchanged(self):
        text = "FROM scratch\n"
        result = docker.rewrite_dockerfile_text(text, registry="10.0.2.100:5000")
        self.assertEqual(result, "FROM scratch\n")

    def test_non_from_lines_unchanged(self):
        text = "RUN echo hello\nCOPY . /app\nCMD [\"python\", \"app.py\"]\n"
        result = docker.rewrite_dockerfile_text(text, registry="10.0.2.100:5000")
        self.assertEqual(result, text)

    def test_multiple_froms(self):
        text = "FROM python:3.11 AS builder\nRUN echo\nFROM alpine:3.18\n"
        result = docker.rewrite_dockerfile_text(text, registry="10.0.2.100:5000")
        expected = "FROM 10.0.2.100:5000/python:3.11 AS builder\nRUN echo\nFROM 10.0.2.100:5000/alpine:3.18\n"
        self.assertEqual(result, expected)

    def test_whitespace_preserved(self):
        text = "  FROM python:3.11\n"
        result = docker.rewrite_dockerfile_text(text, registry="10.0.2.100:5000")
        self.assertTrue(result.startswith("  "))
        self.assertIn("10.0.2.100:5000/python:3.11", result)

    def test_from_with_digest(self):
        text = "FROM python:3.11@sha256:abc123\n"
        result = docker.rewrite_dockerfile_text(text, registry="10.0.2.100:5000")
        self.assertEqual(result, "FROM 10.0.2.100:5000/python:3.11@sha256:abc123\n")

    def test_no_trailing_newline(self):
        text = "FROM python:3.11"
        result = docker.rewrite_dockerfile_text(text, registry="10.0.2.100:5000")
        self.assertEqual(result, "FROM 10.0.2.100:5000/python:3.11")


class TestStripFileArgs(unittest.TestCase):
    def test_short_flag(self):
        out, files = docker.strip_file_args(["-f", "compose.yml", "up"])
        self.assertEqual(out, ["up"])
        self.assertEqual(files, ["compose.yml"])

    def test_long_flag(self):
        out, files = docker.strip_file_args(["--file", "compose.yml", "up"])
        self.assertEqual(out, ["up"])
        self.assertEqual(files, ["compose.yml"])

    def test_equals_flag(self):
        out, files = docker.strip_file_args(["--file=compose.yml", "up"])
        self.assertEqual(out, ["up"])
        self.assertEqual(files, ["compose.yml"])

    def test_no_file_args(self):
        out, files = docker.strip_file_args(["up", "-d"])
        self.assertEqual(out, ["up", "-d"])
        self.assertEqual(files, [])

    def test_multiple_file_args(self):
        out, files = docker.strip_file_args(["-f", "a.yml", "-f", "b.yml", "up"])
        self.assertEqual(out, ["up"])
        self.assertEqual(files, ["a.yml", "b.yml"])


class TestEnvWithBuildkitOff(unittest.TestCase):
    def test_buildkit_off(self):
        env = docker.env_with_buildkit_off()
        self.assertEqual(env["DOCKER_BUILDKIT"], "0")
        self.assertEqual(env["COMPOSE_DOCKER_CLI_BUILD"], "0")

    def test_copies_existing_env(self):
        os.environ["TEST_VAR"] = "test_value"
        env = docker.env_with_buildkit_off()
        self.assertEqual(env["TEST_VAR"], "test_value")
        del os.environ["TEST_VAR"]


class TestTempFileSameDir(unittest.TestCase):
    def test_created_in_same_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "Dockerfile")
            with open(test_file, "w") as f:
                f.write("FROM python:3.11\n")
            result = docker.temp_file_same_dir(test_file, ".Dockerfile")
            self.assertEqual(os.path.dirname(result), tmpdir)
            self.assertTrue(result.startswith(os.path.join(tmpdir, ".Dockerfile.rewritten.")))
            self.assertTrue(os.path.exists(result))
            # cleanup
            os.unlink(result)


class TestRewriteDockerfile(unittest.TestCase):
    def test_rewrites_unqualified(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".Dockerfile", delete=False) as f:
            f.write("FROM python:3.11\nRUN echo\n")
            path = f.name
        try:
            result = docker.rewrite_dockerfile(path, registry="10.0.2.100:5000")
            self.assertNotEqual(result, path)
            with open(result, "r") as f:
                content = f.read()
            self.assertIn("10.0.2.100:5000/python:3.11", content)
            os.unlink(result)
        finally:
            os.unlink(path)

    def test_unchanged_when_no_rewrite_needed(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".Dockerfile", delete=False) as f:
            f.write("FROM scratch\nRUN echo\n")
            path = f.name
        try:
            result = docker.rewrite_dockerfile(path, registry="10.0.2.100:5000")
            self.assertEqual(result, path)
        finally:
            os.unlink(path)

    def test_nonexistent_file(self):
        result = docker.rewrite_dockerfile("/nonexistent/Dockerfile", registry="10.0.2.100:5000")
        self.assertEqual(result, "/nonexistent/Dockerfile")


class TestRewriteComposeDoc(unittest.TestCase):
    def test_simple_image(self):
        doc = {"services": {"web": {"image": "python:3.11"}}}
        result = docker.rewrite_compose_doc(doc, ".", registry="10.0.2.100:5000")
        self.assertEqual(result["services"]["web"]["image"], "10.0.2.100:5000/python:3.11")

    def test_qualified_image_unchanged(self):
        doc = {"services": {"web": {"image": "localhost/foo"}}}
        result = docker.rewrite_compose_doc(doc, ".", registry="10.0.2.100:5000")
        self.assertEqual(result["services"]["web"]["image"], "localhost/foo")

    def test_other_keys_unchanged(self):
        doc = {"services": {"web": {"image": "python:3.11", "ports": ["8080:80"]}}}
        result = docker.rewrite_compose_doc(doc, ".", registry="10.0.2.100:5000")
        self.assertEqual(result["services"]["web"]["image"], "10.0.2.100:5000/python:3.11")
        self.assertEqual(result["services"]["web"]["ports"], ["8080:80"])

    def test_nested_services(self):
        doc = {
            "services": {
                "web": {"image": "python:3.11"},
                "db": {"image": "postgres:15"},
            }
        }
        result = docker.rewrite_compose_doc(doc, ".", registry="10.0.2.100:5000")
        self.assertEqual(result["services"]["web"]["image"], "10.0.2.100:5000/python:3.11")
        self.assertEqual(result["services"]["db"]["image"], "10.0.2.100:5000/postgres:15")

    def test_none_doc(self):
        result = docker.rewrite_compose_doc(None, ".", registry="10.0.2.100:5000")
        self.assertIsNone(result)

    def test_list_doc(self):
        doc = ["item1", "item2"]
        result = docker.rewrite_compose_doc(doc, ".", registry="10.0.2.100:5000")
        self.assertEqual(result, ["item1", "item2"])

    def test_build_dict_deep_copy(self):
        doc = {
            "services": {
                "web": {
                    "build": {
                        "context": ".",
                        "dockerfile": "Dockerfile",
                        "args": {"VERSION": "1.0"},
                        "labels": {"key": "value"},
                    }
                }
            }
        }
        result = docker.rewrite_compose_doc(doc, ".", registry="10.0.2.100:5000")
        # Modify the result - should not affect the original
        result["services"]["web"]["build"]["args"]["VERSION"] = "2.0"
        self.assertEqual(doc["services"]["web"]["build"]["args"]["VERSION"], "1.0")


class TestComposeDefaultFiles(unittest.TestCase):
    def test_no_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                result = docker.compose_default_files()
                self.assertEqual(result, [])
            finally:
                os.chdir(old_cwd)

    def test_finds_compose_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                open("compose.yaml", "w").close()
                result = docker.compose_default_files()
                self.assertEqual(result, ["compose.yaml"])
            finally:
                os.chdir(old_cwd)

    def test_respects_compose_file_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                open("custom.yml", "w").close()
                os.environ["COMPOSE_FILE"] = "custom.yml"
                try:
                    result = docker.compose_default_files()
                    self.assertEqual(result, ["custom.yml"])
                finally:
                    del os.environ["COMPOSE_FILE"]
            finally:
                os.chdir(old_cwd)

    def test_compose_file_multiple_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                open("a.yml", "w").close()
                open("b.yml", "w").close()
                os.environ["COMPOSE_FILE"] = os.pathsep.join(["a.yml", "b.yml"])
                try:
                    result = docker.compose_default_files()
                    self.assertEqual(result, ["a.yml", "b.yml"])
                finally:
                    del os.environ["COMPOSE_FILE"]
            finally:
                os.chdir(old_cwd)

    def test_compose_file_skips_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                open("exists.yml", "w").close()
                os.environ["COMPOSE_FILE"] = "exists.yml" + os.pathsep + "missing.yml"
                try:
                    result = docker.compose_default_files()
                    self.assertEqual(result, ["exists.yml"])
                finally:
                    del os.environ["COMPOSE_FILE"]
            finally:
                os.chdir(old_cwd)

    def test_compose_file_ignored_when_no_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                # No compose files in CWD
                result = docker.compose_default_files()
                self.assertEqual(result, [])
            finally:
                os.chdir(old_cwd)


class TestSkipFlagArgs(unittest.TestCase):
    def test_boolean_flag_does_not_consume(self):
        result = docker._skip_flag_args(["--quiet", "python:3.11"], 0)
        self.assertEqual(result, 1)

    def test_boolean_flag_rm(self):
        result = docker._skip_flag_args(["--rm", "python:3.11"], 0)
        self.assertEqual(result, 1)

    def test_boolean_flag_d(self):
        result = docker._skip_flag_args(["-d", "python:3.11"], 0)
        self.assertEqual(result, 1)

    def test_boolean_flag_q(self):
        result = docker._skip_flag_args(["-q", "python:3.11"], 0)
        self.assertEqual(result, 1)

    def test_boolean_flag_a(self):
        result = docker._skip_flag_args(["-a", "python:3.11"], 0)
        self.assertEqual(result, 1)

    def test_boolean_flag_help(self):
        result = docker._skip_flag_args(["--help"], 0)
        self.assertEqual(result, 1)

    def test_boolean_flag_privileged(self):
        result = docker._skip_flag_args(["--privileged"], 0)
        self.assertEqual(result, 1)

    def test_boolean_flag_interactive(self):
        result = docker._skip_flag_args(["--interactive"], 0)
        self.assertEqual(result, 1)

    def test_boolean_flag_t(self):
        result = docker._skip_flag_args(["-t", "python:3.11"], 0)
        self.assertEqual(result, 1)

    def test_multiple_boolean_flags(self):
        result = docker._skip_flag_args(["--rm", "-d", "-q", "python:3.11"], 0)
        self.assertEqual(result, 3)

    def test_boolean_flag_then_equals_flag(self):
        result = docker._skip_flag_args(["--rm", "-e=FOO", "python:3.11"], 0)
        self.assertEqual(result, 2)

    def test_unknown_flag_still_consumes(self):
        result = docker._skip_flag_args(["--unknown-flag", "python:3.11"], 0)
        self.assertEqual(result, 2)

    def test_boolean_flag_no_next_arg(self):
        result = docker._skip_flag_args(["--quiet"], 0)
        self.assertEqual(result, 1)

    def test_boolean_flag_after_value_flag(self):
        result = docker._skip_flag_args(["--name", "mycontainer", "--rm", "python:3.11"], 0)
        self.assertEqual(result, 3)


class TestRewritePushImage(unittest.TestCase):
    def test_plain(self):
        result = docker.rewrite_push_image(["python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["10.0.2.100:5000/python:3.11"])

    def test_with_all_tags(self):
        result = docker.rewrite_push_image(["--all-tags", "python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["--all-tags", "10.0.2.100:5000/python:3.11"])

    def test_with_short_a(self):
        result = docker.rewrite_push_image(["-a", "python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["-a", "10.0.2.100:5000/python:3.11"])

    def test_qualified_unchanged(self):
        result = docker.rewrite_push_image(["--all-tags", "localhost/foo"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["--all-tags", "localhost/foo"])

    def test_no_args(self):
        result = docker.rewrite_push_image([], registry="10.0.2.100:5000")
        self.assertEqual(result, [])

    def test_with_other_flag(self):
        result = docker.rewrite_push_image(["--creds", "user:pass", "python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["--creds", "user:pass", "10.0.2.100:5000/python:3.11"])


class TestRewriteTagArgs(unittest.TestCase):
    def test_simple(self):
        result = docker.rewrite_tag_args(["python:3.11", "myrepo/python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["10.0.2.100:5000/python:3.11", "10.0.2.100:5000/myrepo/python:3.11"])

    def test_with_flag(self):
        result = docker.rewrite_tag_args(["-f", "file", "python:3.11", "myrepo/python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["-f", "file", "10.0.2.100:5000/python:3.11", "10.0.2.100:5000/myrepo/python:3.11"])

    def test_only_source(self):
        result = docker.rewrite_tag_args(["python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["10.0.2.100:5000/python:3.11"])

    def test_qualified_source(self):
        result = docker.rewrite_tag_args(["localhost/foo", "myrepo/foo"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["localhost/foo", "10.0.2.100:5000/myrepo/foo"])

    def test_qualified_target(self):
        result = docker.rewrite_tag_args(["python:3.11", "localhost/myrepo"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["10.0.2.100:5000/python:3.11", "localhost/myrepo"])


class TestRewriteCommitArgs(unittest.TestCase):
    def test_with_repository(self):
        result = docker.rewrite_commit_args(["mycontainer", "myrepo/image:latest"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["mycontainer", "10.0.2.100:5000/myrepo/image:latest"])

    def test_without_repository(self):
        result = docker.rewrite_commit_args(["mycontainer"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["mycontainer"])

    def test_with_flag(self):
        result = docker.rewrite_commit_args(["-m", "msg", "mycontainer", "myrepo/image:latest"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["-m", "msg", "mycontainer", "10.0.2.100:5000/myrepo/image:latest"])

    def test_qualified_repository(self):
        result = docker.rewrite_commit_args(["mycontainer", "localhost/myrepo"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["mycontainer", "localhost/myrepo"])

    def test_no_args(self):
        result = docker.rewrite_commit_args([], registry="10.0.2.100:5000")
        self.assertEqual(result, [])


class TestExtractDockerfile(unittest.TestCase):
    def test_short_flag(self):
        df, out = docker._extract_dockerfile(["-f", "Dockerfile.prod", "context"])
        self.assertEqual(df, "Dockerfile.prod")
        self.assertEqual(out, ["context"])

    def test_long_flag(self):
        df, out = docker._extract_dockerfile(["--file", "Dockerfile.prod", "context"])
        self.assertEqual(df, "Dockerfile.prod")
        self.assertEqual(out, ["context"])

    def test_equals_flag(self):
        df, out = docker._extract_dockerfile(["--file=Dockerfile.prod", "context"])
        self.assertEqual(df, "Dockerfile.prod")
        self.assertEqual(out, ["context"])

    def test_no_dockerfile(self):
        df, out = docker._extract_dockerfile(["context"])
        self.assertIsNone(df)
        self.assertEqual(out, ["context"])

    def test_multiple_file_args(self):
        df, out = docker._extract_dockerfile(["-f", "a", "-f", "b", "context"])
        self.assertEqual(df, "b")
        self.assertEqual(out, ["context"])

    def test_flag_order_doesnt_matter(self):
        df, out = docker._extract_dockerfile(["-t", "tag", "-f", "Dockerfile", "context"])
        self.assertEqual(df, "Dockerfile")
        self.assertEqual(out, ["-t", "tag", "context"])


class TestRewriteComposeFile(unittest.TestCase):
    def test_no_change_no_temp_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("services:\n  web:\n    image: localhost/foo\n")
            path = f.name
        try:
            result = docker.rewrite_compose_file(path, registry="10.0.2.100:5000")
            self.assertEqual(result, path)
        finally:
            os.unlink(path)

    def test_rewrite_creates_temp(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("services:\n  web:\n    image: python:3.11\n")
            path = f.name
        try:
            result = docker.rewrite_compose_file(path, registry="10.0.2.100:5000")
            self.assertNotEqual(result, path)
            self.assertTrue(result.endswith(".compose.yml"))
            os.unlink(result)
        finally:
            os.unlink(path)


class TestLogger(unittest.TestCase):
    def test_logger_exists(self):
        self.assertTrue(hasattr(docker, "logger"))
        self.assertIsNotNone(docker.logger)

    def test_logger_propagates(self):
        # Logger should propagate to root which has handlers from basicConfig
        self.assertTrue(docker.logger.propagate)

    def test_log_level_default(self):
        # Default log level - logger level is NOTSET (inherits from root)
        # The effective level is determined by the root logger's level
        self.assertIn(docker.logger.level, (logging.NOTSET, logging.WARNING))

    def test_log_level_attr_exists(self):
        # Verify LOG_LEVEL attribute exists
        self.assertTrue(hasattr(docker, "LOG_LEVEL"))


if __name__ == "__main__":
    unittest.main()

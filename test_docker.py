#!/usr/bin/env python3
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


class TestRewriteFirstImage(unittest.TestCase):
    def test_plain(self):
        result = docker.rewrite_first_image(["python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["10.0.2.100:5000/python:3.11"])

    def test_with_flag_prefix(self):
        # -t is treated as a flag; its next token (not a flag) is skipped as its value
        result = docker.rewrite_first_image(["-t", "python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["-t", "python:3.11"])

    def test_with_long_flag_prefix(self):
        result = docker.rewrite_first_image(["--name", "mycontainer", "python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["--name", "mycontainer", "10.0.2.100:5000/python:3.11"])

    def test_flag_with_equals_value(self):
        # --platform=linux/amd64 starts with - so is_flag returns True, but the next
        # token "python:3.11" is not a flag, so it's skipped as the flag's value
        result = docker.rewrite_first_image(["--platform=linux/amd64", "python:3.11"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["--platform=linux/amd64", "python:3.11"])

    def test_already_qualified(self):
        result = docker.rewrite_first_image(["localhost/foo"], registry="10.0.2.100:5000")
        self.assertEqual(result, ["localhost/foo"])

    def test_no_args(self):
        result = docker.rewrite_first_image([], registry="10.0.2.100:5000")
        self.assertEqual(result, [])


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


if __name__ == "__main__":
    unittest.main()

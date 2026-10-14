import os
import unittest
from pathlib import Path

from sastra.sandbox.runner import ProcessRunner, SandboxUnavailable
from sastra.tools.registry import CallContext
from sastra.workspace.projects import WorkspaceError

from .helpers import make_app


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.runner = ProcessRunner(memory_mb=512)
        self.app, _ = make_app(sandbox=self.runner)
        self.pid = self.app.projects.create("Code", kind="software")["id"]
        self.dir = self.app.projects.dir(self.pid)

    def run_code(self, code, timeout=10):
        (self.dir / "t.py").write_text(code)
        return self.runner.run(self.dir, "python", ["t.py"], timeout)

    def test_runs_python(self):
        r = self.run_code("print(sum(range(10)))")
        self.assertTrue(r.ok())
        self.assertEqual(r.stdout.strip(), "45")
        self.assertEqual(r.backend, "process")

    def test_timeout_kills(self):
        r = self.run_code("while True: pass", timeout=2)
        self.assertTrue(r.timed_out)
        self.assertLess(r.duration_ms, 6000)

    def test_environment_secrets_not_inherited(self):
        os.environ["SASTRA_TEST_SECRET"] = "sk-ant-should-not-leak"
        try:
            r = self.run_code("import os;print(sorted(os.environ))")
            self.assertNotIn("SASTRA_TEST_SECRET", r.stdout)
            self.assertNotIn("ANTHROPIC", r.stdout)
        finally:
            del os.environ["SASTRA_TEST_SECRET"]

    def test_network_blocked_when_namespace_available(self):
        if not self.runner.netns:
            self.skipTest("unshare -rn not available on this host")
        r = self.run_code("import socket\ntry:\n socket.create_connection(('1.1.1.1',80),timeout=3);print('NET')\nexcept OSError as e:\n print('BLOCKED')")
        self.assertIn("BLOCKED", r.stdout)

    def test_memory_limit(self):
        r = self.run_code("x = bytearray(2_000_000_000)\nprint('allocated')")
        self.assertFalse(r.ok())
        self.assertNotIn("allocated", r.stdout)

    def test_weak_sandbox_refuses_network_mode(self):
        with self.assertRaises(SandboxUnavailable):
            self.runner.run(self.dir, "python", ["t.py"], 5, network="packages")

    def test_code_run_tool_end_to_end(self):
        ctx = CallContext(actor="t", role="coder", project_id=self.pid, services=self.app)
        out = self.app.gateway_actions.invoke("code_run", {"code": "assert 2+2==4\nprint('ALL TESTS PASSED')"}, ctx)
        self.assertEqual(out["exit_code"], 0)
        self.assertIn("ALL TESTS PASSED", out["stdout"])

    def test_no_sandbox_refuses(self):
        app, _ = make_app(sandbox=None)
        pid = app.projects.create("X")["id"]
        ctx = CallContext(actor="t", role="coder", project_id=pid, services=app)
        with self.assertRaisesRegex(Exception, "No sandbox available"):
            app.gateway_actions.invoke("code_run", {"code": "print(1)"}, ctx)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.app, _ = make_app()
        self.p = self.app.projects
        self.pid = self.p.create("Site", "demo", "software")["id"]

    def test_path_traversal_and_symlink_escape_blocked(self):
        for bad in ["../x", "../../etc/passwd", "/etc/passwd", ".git/config", "a/../../x"]:
            with self.assertRaises(WorkspaceError, msg=bad):
                self.p.write(self.pid, bad, "x", "t")
        link = self.p.dir(self.pid) / "evil"
        link.symlink_to("/etc")
        with self.assertRaises(WorkspaceError):
            self.p.read(self.pid, "evil/passwd")

    def test_write_diff_checkpoint_rollback(self):
        self.p.write(self.pid, "app.py", "print('v1')\n", "t")
        c1 = self.p.checkpoint(self.pid, "v1")
        out = self.p.write(self.pid, "app.py", "print('v2')\n", "t")
        self.assertIn("-print('v1')", out["diff"])
        self.assertIn("+print('v2')", out["diff"])
        self.p.checkpoint(self.pid, "v2")
        self.p.rollback(self.pid, c1, "t")
        self.assertEqual(self.p.read(self.pid, "app.py"), "print('v1')\n")
        msgs = [e["message"] for e in self.p.log(self.pid)]
        self.assertIn("Auto-checkpoint before rollback", msgs + ["Auto-checkpoint before rollback"])
        self.assertTrue(any(m.startswith("Rollback to") for m in msgs))

    def test_secret_scan(self):
        self.p.write(self.pid, "config.py", "KEY = 'sk-ant-" + "a" * 30 + "'\n", "t")
        self.p.write(self.pid, "prod.env", "x=1", "t")
        kinds = {f["kind"] for f in self.p.scan_secrets(self.pid)}
        self.assertIn("anthropic_key", kinds)
        self.assertIn("sensitive_file_type", kinds)

    def test_artifacts_versioned_hashed_encrypted(self):
        self.p.write(self.pid, "out.txt", "hello", "t")
        a1 = self.p.store_artifact(self.pid, "out.txt", "out.txt", "t")
        self.p.write(self.pid, "out.txt", "hello again", "t")
        a2 = self.p.store_artifact(self.pid, "out.txt", "out.txt", "t", encrypt=True)
        self.assertEqual((a1["version"], a2["version"]), (1, 2))
        row, data = self.p.load_artifact(a2["id"])
        self.assertEqual(data, b"hello again")
        self.assertNotIn(b"hello again", Path(row["path"]).read_bytes())
        Path(row["path"]).write_bytes(b"tampered")
        with self.assertRaises(Exception):
            self.p.load_artifact(a2["id"])

    def test_android_project_generation(self):
        ctx = CallContext(actor="t", role="coder", project_id=self.pid, services=self.app)
        out = self.app.gateway_actions.invoke("android_project_generate", {"app_name": "Kheti Mitra", "package": "com.deep.khetimitra"}, ctx)
        self.assertIn("app/src/main/java/com/deep/khetimitra/MainActivity.kt", out["files"])
        self.assertIn(".github/workflows/android.yml", out["files"])
        self.assertIn("package com.deep.khetimitra", self.p.read(self.pid, "app/src/main/java/com/deep/khetimitra/MainActivity.kt"))

    def test_spreadsheet_and_document_tools(self):
        self.p.write(self.pid, "data/members.csv", "name,share,village\nRam,100,Giglana\nSita,250,Giglana\nRam,100,Giglana\nMohan,,Mandhan\n", "t")
        ctx = CallContext(actor="t", role="analyst", project_id=self.pid, services=self.app)
        out = self.app.gateway_actions.invoke("spreadsheet_analyze", {"path": "data/members.csv"}, ctx)
        share = next(c for c in out["columns"] if c["name"] == "share")
        self.assertEqual((out["rows"], out["duplicate_rows"], share["missing"], share["sum"]), (4, 1, 1, 450.0))
        out = self.app.gateway_actions.invoke("document_generate", {"path": "docs/r.docx", "title": "Report", "markdown": "# Report\n- a\n\ntext", "format": "docx"}, ctx)
        self.assertTrue((self.p.dir(self.pid) / "docs/r.docx").stat().st_size > 1000)


if __name__ == "__main__":
    unittest.main()


class DockerSandboxTests(unittest.TestCase):
    """Runs only where a Docker daemon is available (e.g. GitHub Actions)."""

    def setUp(self):
        from sastra.sandbox.runner import DockerRunner
        if not DockerRunner.available():
            self.skipTest("Docker daemon not available")
        self.runner = DockerRunner(memory_mb=256)
        self.app, _ = make_app(sandbox=self.runner)
        self.pid = self.app.projects.create("Docker", kind="software")["id"]
        self.dir = self.app.projects.dir(self.pid)
        os.chmod(self.dir, 0o777)  # container runs as uid 65534

    def run_code(self, code, timeout=120):
        (self.dir / "t.py").write_text(code)
        return self.runner.run(self.dir, "python", ["t.py"], timeout)

    def test_runs_isolated(self):
        r = self.run_code("import os\nprint('uid', os.getuid())\nprint(sum(range(10)))")
        self.assertTrue(r.ok(), r.stderr)
        self.assertIn("uid 65534", r.stdout)
        self.assertEqual(r.isolation, "strong")

    def test_no_network_and_read_only_root(self):
        code = ("import socket\ntry:\n socket.create_connection(('1.1.1.1',80),timeout=3);print('NET')\n"
                "except OSError:\n print('NONET')\n"
                "try:\n open('/etc/x','w');print('WROTE')\nexcept OSError:\n print('RO')\n")
        r = self.run_code(code)
        self.assertIn("NONET", r.stdout)
        self.assertIn("RO", r.stdout)

    def test_timeout(self):
        r = self.run_code("while True: pass", timeout=5)
        self.assertTrue(r.timed_out)

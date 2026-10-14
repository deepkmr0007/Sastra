import unittest

from sastra.models.adapters import AnthropicAdapter, ChatMessage, ModelRequest, ModelResponse

from .helpers import FakeLLM, make_app, openai_adapter, priced


def req(text="hi", **kw):
    return ModelRequest(messages=[ChatMessage("user", text)], **kw)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.llm = FakeLLM(["Namaste! The answer is 42."])

    def tearDown(self):
        self.llm.close()

    def test_streams_and_accounts_cost_and_privacy(self):
        app, _ = make_app(adapters=[openai_adapter(self.llm.url)])
        priced(app)
        app.gateway.set_preference(["openai_compat"])
        pieces = list(app.gateway.stream(req("question")))
        final = pieces[-1]
        self.assertIsInstance(final, ModelResponse)
        self.assertEqual("".join(p for p in pieces[:-1]), "Namaste! The answer is 42.")
        self.assertEqual((final.provider, final.tokens_in, final.tokens_out), ("openai_compat", 100, 50))
        self.assertAlmostEqual(final.cost_usd, (100 * 1 + 50 * 2) / 1e6)
        self.assertAlmostEqual(app.budget.status()["spent_today_usd"], final.cost_usd, places=9)
        flows = app.privacy.dashboard()["leaves_device"]
        self.assertEqual(flows[0]["provider"], "openai_compat")
        self.assertEqual(self.llm.requests[0]["headers"].get("authorization"), "Bearer test-key")

    def test_private_data_never_sent_to_cloud(self):
        app, _ = make_app(adapters=[openai_adapter(self.llm.url)])
        priced(app)
        r = app.gateway.complete(req("my private note", data_class="private"))
        self.assertEqual(r.provider, "offline")
        self.assertEqual(self.llm.requests, [])
        self.assertTrue(any("not allowed" in n for n in r.notes))

    def test_private_data_allowed_to_local_model(self):
        app, _ = make_app(adapters=[openai_adapter(self.llm.url, local=True)])
        r = app.gateway.complete(req("my private note", data_class="private"))
        self.assertEqual((r.provider, r.cost_usd), ("local", 0.0))

    def test_unknown_price_fails_closed(self):
        app, _ = make_app(adapters=[openai_adapter(self.llm.url)])
        r = app.gateway.complete(req())
        self.assertEqual(r.provider, "offline")
        self.assertTrue(any("No price configured" in n for n in r.notes))
        self.assertEqual(self.llm.requests, [])

    def test_budget_cap_blocks_paid_calls(self):
        app, _ = make_app(adapters=[openai_adapter(self.llm.url)])
        priced(app, pin=1000, pout=1000)  # absurd price -> projected cost over cap
        app.budget.set_limits(0.01, 1)
        r = app.gateway.complete(req())
        self.assertEqual(r.provider, "offline")
        self.assertTrue(any("cap" in n for n in r.notes))

    def test_zero_budget_means_no_paid_usage(self):
        app, _ = make_app(adapters=[openai_adapter(self.llm.url)])
        priced(app)
        app.budget.set_limits(0, 0)
        self.assertEqual(app.gateway.complete(req()).provider, "offline")

    def test_cloud_kill_switch(self):
        app, _ = make_app(adapters=[openai_adapter(self.llm.url)])
        priced(app)
        app.controls.set("cloud_models_enabled", False, "owner")
        self.assertEqual(app.gateway.complete(req()).provider, "offline")

    def test_retries_transient_errors(self):
        llm = FakeLLM(["ok after retry"], fail_first=1)
        try:
            app, _ = make_app(adapters=[openai_adapter(llm.url)])
            priced(app)
            r = app.gateway.complete(req())
            self.assertEqual((r.provider, r.text), ("openai_compat", "ok after retry"))
            self.assertEqual(len(llm.requests), 2)
        finally:
            llm.close()

    def test_falls_back_and_opens_circuit(self):
        llm = FakeLLM(["never"], fail_first=100)
        try:
            app, _ = make_app(adapters=[openai_adapter(llm.url)])
            priced(app)
            for _ in range(2):
                self.assertEqual(app.gateway.complete(req()).provider, "offline")
            self.assertEqual(app.gateway.breaker.state("openai_compat"), "open")
            n = len(llm.requests)
            app.gateway.complete(req())
            self.assertEqual(len(llm.requests), n)  # skipped while open
        finally:
            llm.close()

    def test_anthropic_adapter_sse(self):
        a = AnthropicAdapter("sk-ant-test", "claude-sonnet-5-5", "claude-haiku-5-5", timeout=10, url=self.llm.url + "/v1/messages")
        app, _ = make_app(adapters=[a])
        app.gateway.set_preference(["anthropic"])
        r = app.gateway.complete(req("hello", complexity="simple"))
        self.assertEqual((r.provider, r.model, r.text), ("anthropic", "claude-haiku-5-5", "Namaste! The answer is 42."))
        sent = self.llm.requests[0]
        self.assertEqual(sent["headers"]["x-api-key"], "sk-ant-test")
        self.assertEqual(sent["headers"]["anthropic-version"], "2023-06-01")
        self.assertAlmostEqual(r.cost_usd, (100 * 0.10 + 50 * 0.50) / 1e6)

    def test_offline_adapter_is_honest_and_computes(self):
        from sastra.models.adapters import OfflineAdapter
        from sastra.tools.calculator import calculate
        off = OfflineAdapter(calculate)
        self.assertIn("= 84", off.answer("12*(3+4)", ""))
        self.assertIn("offline mode", off.answer("write me a poem", ""))
        self.assertIn("likes chai", off.answer("what do I like?", "<relevant_memory>\n- likes chai\n</relevant_memory>"))


if __name__ == "__main__":
    unittest.main()

from src.sdk.context import ExecutionContext


class TestRegisterSecret:
    def _make_ctx(self):
        return ExecutionContext(
            user_id="u1", email="e@e.com", name="Test",
            scope="GLOBAL", organization=None,
            is_platform_admin=False, is_function_key=False,
            execution_id="exec-1",
        )

    def test_with_active_context(self):
        from bifrost._context import set_execution_context, clear_execution_context, register_secret
        ctx = self._make_ctx()
        set_execution_context(ctx)
        try:
            register_secret("my-oauth-token-abc")
            assert "my-oauth-token-abc" in ctx._collect_secret_values()
        finally:
            clear_execution_context()

    def test_no_context_is_noop(self):
        from bifrost._context import clear_execution_context, register_secret
        clear_execution_context()
        register_secret("my-oauth-token-abc")  # must not raise

    def test_short_value_excluded(self):
        from bifrost._context import set_execution_context, clear_execution_context, register_secret
        ctx = self._make_ctx()
        set_execution_context(ctx)
        try:
            register_secret("ab")
            assert "ab" not in ctx._collect_secret_values()
        finally:
            clear_execution_context()

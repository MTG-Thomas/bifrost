from src.sdk.context import ExecutionContext, Organization, ROIContext


class TestToPublicDict:

    def test_includes_all_public_fields(self):
        ctx = ExecutionContext(
            user_id="u-123",
            email="jack@test.com",
            name="Jack",
            scope="org-456",
            organization=Organization(id="org-456", name="Acme Corp", is_active=True, is_provider=False),
            is_platform_admin=True,
            is_function_key=False,
            execution_id="exec-789",
            workflow_name="my_workflow",
            is_agent=False,
            public_url="https://bifrost.example.com",
            parameters={"ticket_id": 42},
            startup={"preloaded": True},
            roi=ROIContext(time_saved=15, value=100.0),
        )
        result = ctx.to_public_dict()

        assert result["user_id"] == "u-123"
        assert result["email"] == "jack@test.com"
        assert result["name"] == "Jack"
        assert result["scope"] == "org-456"
        assert result["organization"] == {"id": "org-456", "name": "Acme Corp", "is_active": True, "is_provider": False}
        assert result["is_platform_admin"] is True
        assert result["is_function_key"] is False
        assert result["execution_id"] == "exec-789"
        assert result["workflow_name"] == "my_workflow"
        assert result["is_agent"] is False
        assert result["public_url"] == "https://bifrost.example.com"
        assert result["parameters"] == {"ticket_id": 42}
        assert result["startup"] == {"preloaded": True}
        assert result["roi"] == {"time_saved": 15, "value": 100.0}

    def test_excludes_private_fields(self):
        ctx = ExecutionContext(
            user_id="u-1", email="a@b.com", name="A",
            scope="GLOBAL", organization=None,
            is_platform_admin=False, is_function_key=False,
            execution_id="e-1",
            _config={"secret_key": {"type": "secret", "value": "encrypted"}},
        )
        result = ctx.to_public_dict()
        assert "_config" not in result
        assert "_db" not in result
        assert "_config_resolver" not in result
        assert "_integration_cache" not in result
        assert "_integration_calls" not in result
        assert "_dynamic_secrets" not in result
        assert "_scope_override" not in result

    def test_organization_none_for_global(self):
        ctx = ExecutionContext(
            user_id="u-1", email="a@b.com", name="A",
            scope="GLOBAL", organization=None,
            is_platform_admin=False, is_function_key=False,
            execution_id="e-1",
        )
        result = ctx.to_public_dict()
        assert result["organization"] is None
        assert result["scope"] == "GLOBAL"

    def test_startup_none_when_not_set(self):
        ctx = ExecutionContext(
            user_id="u-1", email="a@b.com", name="A",
            scope="GLOBAL", organization=None,
            is_platform_admin=False, is_function_key=False,
            execution_id="e-1",
        )
        result = ctx.to_public_dict()
        assert result["startup"] is None

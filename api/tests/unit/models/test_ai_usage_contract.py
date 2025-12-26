"""
Unit tests for AI Usage contract models.

Tests Pydantic models for AI usage tracking and model pricing.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.models.contracts.ai_usage import (
    AIModelPricingCreate,
    AIModelPricingListItem,
    AIModelPricingListResponse,
    AIModelPricingPublic,
    AIModelPricingUpdate,
    AIUsageByModel,
    AIUsagePublic,
    AIUsageSummaryResponse,
    AIUsageTotals,
    ConversationUsage,
    OrganizationUsage,
    UsageReportResponse,
    UsageReportSummary,
    UsageTrend,
    WorkflowUsage,
)


class TestAIModelPricingCreate:
    """Tests for AIModelPricingCreate model."""

    def test_valid_creation(self):
        """Test valid pricing creation."""
        pricing = AIModelPricingCreate(
            provider="openai",
            model="gpt-4o",
            input_price_per_million=Decimal("5.00"),
            output_price_per_million=Decimal("15.00"),
        )

        assert pricing.provider == "openai"
        assert pricing.model == "gpt-4o"
        assert pricing.input_price_per_million == Decimal("5.00")
        assert pricing.output_price_per_million == Decimal("15.00")
        assert pricing.effective_date is None

    def test_with_effective_date(self):
        """Test pricing creation with effective date."""
        pricing = AIModelPricingCreate(
            provider="openai",
            model="gpt-4o",
            input_price_per_million=Decimal("5.00"),
            output_price_per_million=Decimal("15.00"),
            effective_date=date(2025, 1, 1),
        )

        assert pricing.effective_date == date(2025, 1, 1)

    def test_provider_max_length(self):
        """Test provider field respects max length."""
        with pytest.raises(ValidationError):
            AIModelPricingCreate(
                provider="x" * 51,  # Exceeds 50 char limit
                model="gpt-4o",
                input_price_per_million=Decimal("5.00"),
                output_price_per_million=Decimal("15.00"),
            )

    def test_model_max_length(self):
        """Test model field respects max length."""
        with pytest.raises(ValidationError):
            AIModelPricingCreate(
                provider="openai",
                model="x" * 101,  # Exceeds 100 char limit
                input_price_per_million=Decimal("5.00"),
                output_price_per_million=Decimal("15.00"),
            )


class TestAIModelPricingUpdate:
    """Tests for AIModelPricingUpdate model."""

    def test_all_fields_optional(self):
        """Test all fields are optional for update."""
        update = AIModelPricingUpdate()

        assert update.input_price_per_million is None
        assert update.output_price_per_million is None
        assert update.effective_date is None

    def test_partial_update(self):
        """Test partial update with only some fields."""
        update = AIModelPricingUpdate(
            input_price_per_million=Decimal("6.00"),
        )

        assert update.input_price_per_million == Decimal("6.00")
        assert update.output_price_per_million is None


class TestAIModelPricingPublic:
    """Tests for AIModelPricingPublic model."""

    def test_serializes_decimals_to_string(self):
        """Test Decimal fields serialize to strings."""
        pricing = AIModelPricingPublic(
            id=1,
            provider="openai",
            model="gpt-4o",
            input_price_per_million=Decimal("5.00"),
            output_price_per_million=Decimal("15.00"),
            effective_date=date(2025, 1, 1),
            created_at=datetime(2025, 1, 1, 12, 0, 0),
            updated_at=datetime(2025, 1, 1, 12, 0, 0),
        )

        data = pricing.model_dump()
        assert data["input_price_per_million"] == "5.00"
        assert data["output_price_per_million"] == "15.00"

    def test_serializes_dates_to_iso_format(self):
        """Test date/datetime fields serialize to ISO format."""
        pricing = AIModelPricingPublic(
            id=1,
            provider="openai",
            model="gpt-4o",
            input_price_per_million=Decimal("5.00"),
            output_price_per_million=Decimal("15.00"),
            effective_date=date(2025, 1, 1),
            created_at=datetime(2025, 1, 1, 12, 0, 0),
            updated_at=datetime(2025, 1, 1, 12, 0, 0),
        )

        data = pricing.model_dump()
        assert data["effective_date"] == "2025-01-01"
        assert "T" in data["created_at"]  # ISO format includes T


class TestAIUsagePublic:
    """Tests for AIUsagePublic model."""

    def test_valid_creation(self):
        """Test valid usage record creation."""
        from uuid import uuid4

        usage = AIUsagePublic(
            id=1,
            provider="openai",
            model="gpt-4o",
            input_tokens=1000,
            output_tokens=500,
            cost=Decimal("0.0125"),
            duration_ms=150,
            execution_id=uuid4(),
            timestamp=datetime.now(),
            sequence=1,
        )

        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500

    def test_cost_can_be_none(self):
        """Test cost field can be None."""
        usage = AIUsagePublic(
            id=1,
            provider="openai",
            model="gpt-4o",
            input_tokens=1000,
            output_tokens=500,
            cost=None,
            timestamp=datetime.now(),
            sequence=1,
        )

        assert usage.cost is None

    def test_tokens_must_be_non_negative(self):
        """Test token counts must be >= 0."""
        with pytest.raises(ValidationError):
            AIUsagePublic(
                id=1,
                provider="openai",
                model="gpt-4o",
                input_tokens=-100,  # Invalid
                output_tokens=500,
                timestamp=datetime.now(),
                sequence=1,
            )

    def test_serializes_cost_to_string_or_none(self):
        """Test cost serializes to string when present, None otherwise."""
        usage_with_cost = AIUsagePublic(
            id=1,
            provider="openai",
            model="gpt-4o",
            input_tokens=1000,
            output_tokens=500,
            cost=Decimal("0.0125"),
            timestamp=datetime.now(),
            sequence=1,
        )

        usage_without_cost = AIUsagePublic(
            id=2,
            provider="openai",
            model="gpt-4o",
            input_tokens=1000,
            output_tokens=500,
            cost=None,
            timestamp=datetime.now(),
            sequence=1,
        )

        assert usage_with_cost.model_dump()["cost"] == "0.0125"
        assert usage_without_cost.model_dump()["cost"] is None


class TestAIUsageTotals:
    """Tests for AIUsageTotals model."""

    def test_defaults_to_zeros(self):
        """Test defaults to zero values."""
        totals = AIUsageTotals()

        assert totals.total_input_tokens == 0
        assert totals.total_output_tokens == 0
        assert totals.total_cost == Decimal("0")
        assert totals.total_duration_ms == 0
        assert totals.call_count == 0

    def test_serializes_cost_to_string(self):
        """Test cost serializes to string."""
        totals = AIUsageTotals(
            total_input_tokens=1000,
            total_output_tokens=500,
            total_cost=Decimal("0.0125"),
            total_duration_ms=150,
            call_count=1,
        )

        data = totals.model_dump()
        assert data["total_cost"] == "0.0125"


class TestAIUsageByModel:
    """Tests for AIUsageByModel model."""

    def test_valid_creation(self):
        """Test valid creation."""
        by_model = AIUsageByModel(
            provider="openai",
            model="gpt-4o",
            input_tokens=2000,
            output_tokens=1000,
            cost=Decimal("0.025"),
            call_count=2,
        )

        assert by_model.provider == "openai"
        assert by_model.call_count == 2


class TestUsageReportSummary:
    """Tests for UsageReportSummary model."""

    def test_defaults_to_zeros(self):
        """Test defaults to zero values."""
        summary = UsageReportSummary()

        assert summary.total_ai_cost == Decimal("0")
        assert summary.total_input_tokens == 0
        assert summary.total_output_tokens == 0
        assert summary.total_ai_calls == 0
        assert summary.total_cpu_seconds == 0.0
        assert summary.peak_memory_bytes == 0


class TestUsageTrend:
    """Tests for UsageTrend model."""

    def test_valid_creation(self):
        """Test valid trend data point."""
        trend = UsageTrend(
            date=date(2025, 1, 15),
            ai_cost=Decimal("1.50"),
            input_tokens=10000,
            output_tokens=5000,
        )

        assert trend.date == date(2025, 1, 15)

    def test_serializes_date_to_iso(self):
        """Test date serializes to ISO format."""
        trend = UsageTrend(
            date=date(2025, 1, 15),
            ai_cost=Decimal("1.50"),
            input_tokens=10000,
            output_tokens=5000,
        )

        data = trend.model_dump()
        assert data["date"] == "2025-01-15"


class TestWorkflowUsage:
    """Tests for WorkflowUsage model."""

    def test_defaults_to_zeros(self):
        """Test defaults to zero values except workflow_name."""
        usage = WorkflowUsage(workflow_name="Test Workflow")

        assert usage.workflow_name == "Test Workflow"
        assert usage.execution_count == 0
        assert usage.input_tokens == 0
        assert usage.ai_cost == Decimal("0")


class TestConversationUsage:
    """Tests for ConversationUsage model."""

    def test_valid_creation(self):
        """Test valid creation."""
        usage = ConversationUsage(
            conversation_id="conv-123",
            conversation_title="Test Conversation",
            message_count=10,
            input_tokens=5000,
            output_tokens=2500,
            ai_cost=Decimal("0.0625"),
        )

        assert usage.conversation_id == "conv-123"
        assert usage.message_count == 10


class TestOrganizationUsage:
    """Tests for OrganizationUsage model."""

    def test_valid_creation(self):
        """Test valid creation."""
        usage = OrganizationUsage(
            organization_id="org-123",
            organization_name="Test Org",
            execution_count=100,
            conversation_count=50,
            input_tokens=100000,
            output_tokens=50000,
            ai_cost=Decimal("12.50"),
        )

        assert usage.organization_id == "org-123"
        assert usage.execution_count == 100


class TestUsageReportResponse:
    """Tests for UsageReportResponse model."""

    def test_defaults_to_empty_lists(self):
        """Test defaults to empty lists."""
        response = UsageReportResponse(summary=UsageReportSummary())

        assert response.trends == []
        assert response.by_workflow == []
        assert response.by_conversation == []
        assert response.by_organization == []


class TestAIModelPricingListItem:
    """Tests for AIModelPricingListItem model."""

    def test_includes_is_used_field(self):
        """Test includes is_used field."""
        item = AIModelPricingListItem(
            id=1,
            provider="openai",
            model="gpt-4o",
            input_price_per_million=Decimal("5.00"),
            output_price_per_million=Decimal("15.00"),
            effective_date=date(2025, 1, 1),
            created_at=datetime(2025, 1, 1, 12, 0, 0),
            updated_at=datetime(2025, 1, 1, 12, 0, 0),
            is_used=True,
        )

        assert item.is_used is True

    def test_is_used_defaults_to_false(self):
        """Test is_used defaults to False."""
        item = AIModelPricingListItem(
            id=1,
            provider="openai",
            model="gpt-4o",
            input_price_per_million=Decimal("5.00"),
            output_price_per_million=Decimal("15.00"),
            effective_date=date(2025, 1, 1),
            created_at=datetime(2025, 1, 1, 12, 0, 0),
            updated_at=datetime(2025, 1, 1, 12, 0, 0),
        )

        assert item.is_used is False


class TestAIModelPricingListResponse:
    """Tests for AIModelPricingListResponse model."""

    def test_defaults_to_empty_lists(self):
        """Test defaults to empty lists."""
        response = AIModelPricingListResponse()

        assert response.pricing == []
        assert response.models_without_pricing == []

    def test_with_data(self):
        """Test with actual data."""
        response = AIModelPricingListResponse(
            pricing=[
                AIModelPricingListItem(
                    id=1,
                    provider="openai",
                    model="gpt-4o",
                    input_price_per_million=Decimal("5.00"),
                    output_price_per_million=Decimal("15.00"),
                    effective_date=date(2025, 1, 1),
                    created_at=datetime(2025, 1, 1, 12, 0, 0),
                    updated_at=datetime(2025, 1, 1, 12, 0, 0),
                    is_used=True,
                )
            ],
            models_without_pricing=["anthropic/claude-unknown"],
        )

        assert len(response.pricing) == 1
        assert len(response.models_without_pricing) == 1


class TestAIUsageSummaryResponse:
    """Tests for AIUsageSummaryResponse model."""

    def test_defaults_to_empty_lists(self):
        """Test defaults to empty lists."""
        response = AIUsageSummaryResponse(totals=AIUsageTotals())

        assert response.by_model == []
        assert response.usage == []

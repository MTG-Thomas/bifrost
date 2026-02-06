"""Tests for bifrost sync command."""
from bifrost.sync import format_preview_summary


class TestFormatPreviewSummary:
    """Test preview output formatting."""

    def test_empty_sync(self):
        """Should report no changes when preview is empty."""
        preview = {
            "to_pull": [],
            "to_push": [],
            "conflicts": [],
            "will_orphan": [],
            "is_empty": True,
        }
        lines = format_preview_summary(preview)
        assert any("no changes" in line.lower() for line in lines)

    def test_clean_sync(self):
        """Should summarize pull/push counts without conflicts."""
        preview = {
            "to_pull": [
                {"path": "workflows/a.py", "action": "add", "display_name": "a"},
                {"path": "workflows/b.py", "action": "modify", "display_name": "b"},
            ],
            "to_push": [
                {"path": "workflows/c.py", "action": "add", "display_name": "c"},
            ],
            "conflicts": [],
            "will_orphan": [],
            "is_empty": False,
        }
        lines = format_preview_summary(preview)
        text = "\n".join(lines)
        assert "2" in text  # 2 to pull
        assert "1" in text  # 1 to push

    def test_conflicts_shown(self):
        """Should list each conflict with path and resolve command."""
        preview = {
            "to_pull": [],
            "to_push": [],
            "conflicts": [
                {
                    "path": "workflows/billing.py",
                    "display_name": "billing",
                    "entity_type": "workflow",
                },
            ],
            "will_orphan": [],
            "is_empty": False,
        }
        lines = format_preview_summary(preview)
        text = "\n".join(lines)
        assert "workflows/billing.py" in text
        assert "--resolve" in text

    def test_orphans_shown(self):
        """Should warn about orphaned workflows."""
        preview = {
            "to_pull": [],
            "to_push": [],
            "conflicts": [],
            "will_orphan": [
                {
                    "workflow_id": "abc",
                    "workflow_name": "Process Ticket",
                    "function_name": "process_ticket",
                    "last_path": "workflows/tickets.py",
                    "used_by": [
                        {"type": "form", "id": "def", "name": "Ticket Form"},
                    ],
                },
            ],
            "is_empty": False,
        }
        lines = format_preview_summary(preview)
        text = "\n".join(lines)
        assert "Process Ticket" in text
        assert "Ticket Form" in text

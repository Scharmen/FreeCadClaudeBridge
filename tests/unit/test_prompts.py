"""The panel prompts stay short and point to the skill instead of repeating it."""

from claudebridge.panel import prompts


def test_both_prompts_share_the_rules_and_stay_under_two_thousand_characters():
    assert prompts.RULES in prompts.SYSTEM_PROMPT and prompts.RULES in prompts.FULL_CHAT_PROMPT
    assert len(prompts.SYSTEM_PROMPT) < 2000 and len(prompts.FULL_CHAT_PROMPT) < 2000
    for text in (prompts.SYSTEM_PROMPT, prompts.FULL_CHAT_PROMPT):
        assert "freecad skill" in text and "check_all" in text and "design_notes" in text
        assert "feedback itself" in text


def test_full_chat_installs_missing_programs_after_asking():
    """A missing program (pdftoppm for PDF pages) is not shipped with the plugin: Claude asks, then installs."""
    text = prompts.FULL_CHAT_PROMPT
    assert "missing on the machine" in text and "ask the user" in text and "package manager" in text
    assert "with their content" in text and "[File/CAD file/Folder" in text

"""Prompt template builder that renders system prompts from POML template files."""

from pathlib import Path

from poml import poml as render_poml

_DEFAULT_TEMPLATES_DIR = Path(__file__).parent / "templates"


class PromptBuilder:
    """Renders a POML template file into a system prompt string.

    The template uses POML semantic tags (``<role>``, ``<output-format>``,
    ``<section>``) and ``{{ variable }}`` syntax for substitution.
    """

    def __init__(self, template: Path | None = None) -> None:
        self._template = template or (_DEFAULT_TEMPLATES_DIR / "system_prompt.poml")
        if not self._template.exists():
            msg = f"Template file not found: {self._template}"
            raise FileNotFoundError(msg)

    def build(self, **variables: str) -> str:
        """Render the POML template with the given variables."""
        result = render_poml(
            self._template,
            context=variables or None,
            chat=False,
            format="dict",
        )
        return result["messages"]

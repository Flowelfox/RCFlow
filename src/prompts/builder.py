"""Prompt template builder that renders system prompts from Jinja2 template files."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from src.paths import get_templates_dir

_DEFAULT_TEMPLATES_DIR = get_templates_dir()


class PromptBuilder:
    """Renders a Jinja2 template file into a system prompt string.

    The template uses Jinja2 ``{{ variable }}`` syntax for substitution.
    Variables are required — missing variables raise an ``UndefinedError``.
    """

    def __init__(self, template: Path | None = None) -> None:
        self._template_path = template or (_DEFAULT_TEMPLATES_DIR / "system_prompt.j2")
        if not self._template_path.exists():
            msg = f"Template file not found: {self._template_path}"
            raise FileNotFoundError(msg)
        self._env = Environment(
            loader=FileSystemLoader(str(self._template_path.parent)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )

    def build(self, **variables: str) -> str:
        """Render the Jinja2 template with the given variables."""
        template = self._env.get_template(self._template_path.name)
        return template.render(**variables)

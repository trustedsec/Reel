"""
Sandboxed Jinja2 template rendering for victim-facing surfaces.

Replaces the legacy `interpolate_string` + `render_template_string` two-pass,
which let victim-controlled values be re-parsed as Jinja (SSTI). This module
parses each template exactly once, inside a SandboxedEnvironment with
autoescape on. Variable values can never be re-parsed; Python escape gadgets
(`__class__`, `__mro__`, `__globals__`, ...) raise SecurityError at render.
"""
from jinja2.sandbox import SandboxedEnvironment

_env = SandboxedEnvironment(autoescape=True)


def render_sandboxed(template_html: str, context: dict) -> str:
    return _env.from_string(template_html or '').render(**context)

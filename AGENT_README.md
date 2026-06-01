# Agent README

This document explains core concepts of the Reel codebase for AI agents (e.g. Cursor, Copilot). Use it to understand architecture, the plugin system, style guides, and how to implement changes consistently. It includes enough detail to build a plugin from scratch. For full documentation, see [DEVELOPMENT.md](DEVELOPMENT.md).

---

## Codebase structure

**Dual Flask apps**

- [app.py](app.py) defines two application factories: `create_app()` (phishing server, default port 1234) and `create_admin_app()` (admin UI, default port 8000). They share configuration and database; blueprints and entry points differ.

**Key packages**

- **admin/** — Routes and Jinja2 templates for the admin UI (campaigns, workflows, plugins, settings, etc.).
- **api/** — REST routes, Pydantic models, and services (plugin service, workflow service). Used by the admin UI and external integrations.
- **phishing/** — Campaign routes and workflow executor for GET/POST requests. Serves campaign landing pages by UID and runs inbound workflows.
- **plugins/** — Base plugin interface, registry, and built-in plugins. All workflow nodes are plugins.
- **workflows/** — Workflow engine, sending executor, hooks, rate limiter, retry processor, scheduler. Orchestrates node execution and outbound sending.
- **shared/** — Database, auth, config, workflow variables, errors, turnstile/CAPTCHA, phishing detector service, etc. Shared across both apps.

**Data flow (inbound)**

1. Request hits the phishing server by campaign UID (e.g. `/<uid>` or `/<uid>/path`).
2. [phishing/workflow_executor.py](phishing/workflow_executor.py) builds the execution context (campaign, request, session, variables).
3. [workflows/engine.py](workflows/engine.py) runs the workflow: start node, then plugin/condition/loop/end nodes in order according to connections.
4. Plugin nodes call `get_plugin(plugin_type).execute(context, config)`; the returned context is passed to the next node.
5. The executor converts context to an HTTP response using `_response_html`, `_response_redirect`, `_response_json`, and related keys (see [phishing/workflow_executor.py](phishing/workflow_executor.py) `_context_to_response`).

**Data flow (outbound)**

1. Operator triggers a sending workflow from the admin UI.
2. [workflows/sending_executor.py](workflows/sending_executor.py) runs the sending workflow: target selection, optional validation/pre-render, then a loop over targets.
3. For each target, the workflow renders content (e.g. email), applies rate limiting, and sends via a plugin (e.g. SMTP). Results are recorded.

---

## Plugin system (for agents)

**Definition**

- All workflow plugin nodes implement [plugins/base.py](plugins/base.py) `BasePlugin`: required properties and `execute(context, config)` returning an updated context dict.

**Discovery**

- **Built-in:** Modules under [plugins/builtin/](plugins/builtin/) are imported and classes inheriting `BasePlugin` are auto-registered at startup.
- **Custom:** Uploaded via API; [plugins/registry.py](plugins/registry.py) `load_plugin_from_path()` loads the file and registers the first `BasePlugin` subclass. Stored in DB with `code_path`. Not loaded from DB on app startup; only in memory after upload until restart.

**Execution**

- [workflows/engine.py](workflows/engine.py) `_execute_plugin_node()`: resolves `plugin_type` from the node, gets `plugin = get_plugin(plugin_type)`, reads `config = node.config`, runs `plugin.validate_config(config)`, then `plugin.execute(context.copy(), config)`. Exceptions are passed to `plugin.on_error()`.

**Branching**

- Nodes can have two outgoing connections labeled "true" and "false". The engine calls `plugin.get_branch_context_key()` to get the context key (e.g. `validation_passed`), reads that value from the returned context, and follows the "true" or "false" connection. Plugins that branch (e.g. Validate Input, Conditional) set this key to a boolean or truthy/falsy value.

---

## Building a plugin from scratch

This section gives an agent everything needed to implement a plugin. Reference: [DEVELOPMENT.md](DEVELOPMENT.md).

### BasePlugin interface

```python
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List

class BasePlugin(ABC):
    @property
    @abstractmethod
    def plugin_type(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def plugin_category(self) -> str: ...

    @property
    @abstractmethod
    def config_schema(self) -> Dict[str, Any]: ...

    @abstractmethod
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]: ...

    # Optional overrides (default implementations exist)
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]: ...
    def get_branch_context_key(self) -> Optional[str]: ...
    def on_error(self, error: Exception, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]: ...
```

### Minimal plugin template

```python
from typing import Dict, Any
from plugins.base import BasePlugin

class ExamplePlugin(BasePlugin):
    @property
    def plugin_type(self) -> str:
        return "example"

    @property
    def display_name(self) -> str:
        return "Example Plugin"

    @property
    def description(self) -> str:
        return "Minimal plugin that passes context through. Use as a starting point."

    @property
    def plugin_category(self) -> str:
        return "data_transform"

    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "title": "Message",
                    "description": "Optional message to add to context"
                }
            }
        }

    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        if config.get("message"):
            context = context.copy()
            context["example_message"] = config["message"]
        return context
```

### Execution contract

**Input:** `context` (dict with `campaign`, `request`, `session`, `variables`; may include outputs from previous nodes). `config` is the node config (user-filled values from `config_schema`).

**Context shape (inbound):** `campaign` (id, uid, name, template_html, config), `request` (method, path, form_data, query_params, ip_address, user_agent, headers), `session`, `variables`. Sending workflows add `target`, `targets`, `sending_workflow_id`.

**Output:** Return a dict (updated context). Never return `None`.

**Response keys (inbound campaigns):** To drive the HTTP response, set one of: `_response_html`, `_response_redirect`, `_response_json`. Optionally: `_response_status`, `_response_headers`. For session updates: `_set_session` dict.

**Branching:** If the plugin has two outcomes (pass/fail), set a context key (e.g. `validation_passed`) to a boolean and implement `get_branch_context_key()` to return that key name. The engine follows the "true" or "false" connection from the node.

**Variable interpolation:** For config strings that may contain `{{ request.form_data.email }}` or `{{ campaign.uid }}`, use `interpolate_string(template, context)` from `shared.workflow_variables`.

### Config schema and UI

The workflow builder renders the config form from `config_schema`. Use JSON Schema structure with `type`, `properties`, `required`.

**Top-level:** `type: "object"`, `properties`, `required` (array of required field names).

**Per-property keys:**

| Key | Purpose | UI behavior |
|-----|---------|-------------|
| `type` | Data type | `string` (text/textarea), `integer`/`number`, `boolean` (checkbox), `array` (dynamic list), `object` (nested form or JSON textarea). |
| `title` | Field label | Shown as label. Fallback: `title` > `description` > key. |
| `description` | Secondary text | Help text when `title` present; else used as label. |
| `help` | Tooltip | Shown on info icon hover. |
| `placeholder` | Input placeholder | Placeholder for text inputs. |
| `default` | Default value | Pre-filled; shows "(default)" badge. |
| `format` | String hint | For `type: "string"`: `"email"`, `"uri"`/`"url"`, `"textarea"` (multiline). |
| `enum` | Dropdown | Renders `<select>`. See format below. |
| `properties` | Nested object | For `type: "object"`: renders nested form fields. |
| `items` | Array item schema | For `type: "array"`: if `items.properties` exists, each item is a mini-form. |
| `required` | Nested required | For nested objects: array of required nested keys. |

**Enum format:** Simple `["opt1", "opt2"]` (value = label). Or object: `{"value": 302, "label": "302 Found", "description": "Tooltip"}` — `value` stored, `label` shown, `description` optional.

**Nested objects:** Use `type: "object"` with `properties` and optional `required`. Example: [plugins/builtin/conditional.py](plugins/builtin/conditional.py) `condition` object.

**Arrays:** Use `type: "array"` with `items`. If `items.properties` exists, each element is a mini-form with "Add Item" button. Example: [plugins/builtin/validate_input.py](plugins/builtin/validate_input.py) `fields` array.

**Special:** `x-optionsSource: "templates"` — dynamic dropdown from `/api/templates`. Union types `type: ["string", "number"]` supported; UI uses first type.

**Example config_schema:**

```python
"config_schema": {
    "type": "object",
    "properties": {
        "message": {
            "type": "string",
            "title": "Message",
            "placeholder": "Enter a message"
        },
        "mode": {
            "type": "string",
            "title": "Mode",
            "enum": [
                {"value": "simple", "label": "Simple"},
                {"value": "advanced", "label": "Advanced"}
            ],
            "default": "simple"
        }
    },
    "required": ["message"]
}
```

### Plugin categories

Use one of: `campaign`, `sending`, `target_selection`, `email_validation`, `notification`, `data_transform`, `conditional`, `delay`, `capture`, `logging`, or another existing category that fits. Affects workflow builder filter.

### Adding as built-in

1. Create `plugins/builtin/my_plugin.py`.
2. Implement the class and all required properties.
3. In [plugins/builtin/__init__.py](plugins/builtin/__init__.py): add `from .my_plugin import MyPlugin` and add `MyPlugin` to `__all__`.
4. Run `pytest tests/test_plugins/ -v` and add a test in `tests/test_plugins/` if appropriate.

### Agent checklist

1. Subclass `BasePlugin`. Implement: `plugin_type`, `display_name`, `description`, `plugin_category`, `config_schema`, `execute(context, config)`.
2. Return a dict (updated context); never return `None`.
3. For HTTP response: set `_response_html`, `_response_redirect`, or `_response_json` (and optionally `_response_status`, `_response_headers`). For session: `_set_session`.
4. For config strings with `{{ variables }}`: use `interpolate_string(s, context)` from `shared.workflow_variables`.
5. For branching: set a boolean in context and implement `get_branch_context_key()`.
6. For config validation: override `validate_config(config)`; return list of error strings or `None`.

---

## Conventions and patterns

- **Python:** Guard clauses, early returns, type hints (e.g. `str | None`), `pathlib` for paths. Project targets Python 3.10+ and uses uv for dependencies.
- **Imports:** Plugin code may use `shared.*` and `plugins.base`. Avoid circular imports; plugins must not import the workflow engine or app.
- **Context shape (inbound):** `campaign` (id, uid, name, template_html, config), `request` (method, path, form_data, query_params, ip_address, user_agent, headers), `session`, `variables`. Sending workflows add `target`, `targets`, `sending_workflow_id`, and related keys.

---

## Style guides

Agents must follow the project’s style guides when editing code or UI.

### Python

- **Language:** Python 3.10 minimum. Use 3.10+ features where appropriate: `match`/`case`, union types as `str | None`, `pathlib` for paths.
- **Control flow:** Prefer guard clauses and early returns; avoid deep nesting (max 2–3 levels). Validate inputs first, then run main logic.
- **Functions:** Single responsibility, max ~20–30 lines. Prefer pure functions when possible. Use descriptive names.
- **Types:** Use type hints consistently. Prefer `str | None` over `Optional[str]`. Use `TypeAlias` for complex types, `Final` for constants.
- **Errors:** Prefer specific exceptions; use guard clauses to fail fast. Consider a Result-like pattern where it helps.
- **Dependencies:** Use **uv** (not pip): `uv add <pkg>`, `uv run`, `uv sync`. Pin versions in production. Project config in `pyproject.toml` if present, else `requirements.txt`.
- **Structure:** Prefer composition over inheritance. Small, focused classes. Use dataclasses or Pydantic for data. Prefer API/JSON over form parsing for frontend-facing endpoints.
- **Formatting:** f-strings, list/dict comprehensions when readable. Context managers for resources. No business logic in the View layer.

### Data flow (MVC)

- **Model:** Data fetching, transformation, and business logic live in the Model layer or service modules. Do not put these inside frontend components.
- **View:** UI components and pages only render state and handle presentation. No direct data mutation or fetch calls in the View.
- **Controller:** Use a Controller or intermediary (hooks, controllers, Redux/Zustand/Vuex, etc.) between Model and View. When unsure: Model exposes data and interfaces; Controller coordinates and updates state; View subscribes and renders.

### Frontend / UI (admin and campaign templates)

- **Design systems:** Follow **Neobrutalism** and **Airbnb-style** principles: unified, minimal, clear hierarchy. Prefer **Catppuccin Macchiato** for color. Use Tailwind for styling.
- **Neobrutalism:** Thick black borders (`border-2`/`border-4`, `border-black`). Hard, offset shadows (e.g. `shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]`). Bold, saturated colors (`bg-yellow-300`, `bg-cyan-400`, `bg-lime-300`). Sharp or minimal rounding (`rounded-none`, `rounded-sm`). Buttons: primary with shadow that shrinks on hover/active; secondary can grow shadow. No gradients, blur, or soft shadows.
- **Airbnb principles:** Unified components, responsive layouts, clear navigation and hierarchy. Minimal constraints with purpose; standardize typography and interaction patterns. Keep UI practical and maintainable.
- **Catppuccin Macchiato:** Use semantic names where possible. Key colors: Text `#cad3f5`, Subtext1 `#b8c0e0`, Base `#24273a`, Mantle `#1e2030`, Crust `#181926`; Surface0 `#363a4f`, Surface1 `#494d64`, Surface2 `#5b6078`; accent colors Rosewater `#f4dbd6`, Flamingo `#f0c6c6`, Pink `#f5bde6`, Mauve `#c6a0f6`, Red `#ed8796`, Maroon `#ee99a0`, Peach `#f5a97f`, Yellow `#eed49f`, Green `#a6da95`, Teal `#8bd5ca`, Sky `#91d7e3`, Sapphire `#7dc4e4`, Blue `#8aadf4`, Lavender `#b7bdf8`. Prefer these over arbitrary hex so the app stays on-palette.
- **Accessibility:** Sufficient contrast (WCAG AA), clear focus indicators (e.g. visible shadows), minimum touch/click targets (e.g. `px-4 py-2`).
- **Do not:** Use gradient backgrounds, blur, thin borders, `rounded-full` (except avatars), or muted low-contrast colors unless explicitly requested.

When adding or changing UI, align with existing admin templates in `admin/templates/` and the Tailwind/Neobrutalism patterns used there.

---

## Useful prompts for agents

- "Add a new built-in plugin that does X; follow the pattern in plugins/builtin/log_event.py."
- "This plugin should branch on pass/fail; set a context key and implement get_branch_context_key."
- "Validate config in validate_config and return a list of error strings for invalid fields."
- "Use interpolate_string for any config value that may contain {{ variables }}."
- "Add a dropdown to config_schema using enum with value/label/description objects."
- "Run plugin tests: pytest tests/test_plugins/ -v"

---

## Key files reference

| Purpose | File |
|--------|------|
| Full plugin documentation | [DEVELOPMENT.md](DEVELOPMENT.md) |
| Plugin interface | [plugins/base.py](plugins/base.py) |
| Registry and loading | [plugins/registry.py](plugins/registry.py) |
| Workflow execution | [workflows/engine.py](workflows/engine.py) (`_execute_plugin_node`, `_resolve_boolean_path`) |
| Context to HTTP response | [phishing/workflow_executor.py](phishing/workflow_executor.py) (`_context_to_response`) |
| Variable interpolation | [shared/workflow_variables.py](shared/workflow_variables.py) |
| Workflow builder (config form) | [admin/templates/workflow_builder.html](admin/templates/workflow_builder.html) |
| Example plugins | [plugins/builtin/conditional.py](plugins/builtin/conditional.py), [plugins/builtin/log_event.py](plugins/builtin/log_event.py), [plugins/builtin/redirect.py](plugins/builtin/redirect.py), [plugins/builtin/validate_input.py](plugins/builtin/validate_input.py) |

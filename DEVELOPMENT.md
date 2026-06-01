# Development Guide

This document describes how to develop custom plugins for Reel. For general setup and project overview, see [README.md](README.md).

---

## Overview

Plugins are workflow nodes. Each plugin implements the `BasePlugin` interface in [plugins/base.py](plugins/base.py): `plugin_type`, `display_name`, `description`, `plugin_category`, `config_schema`, and `execute(context, config)`.

There are two ways to add a plugin:

- **Built-in:** Code lives in the repository and is auto-discovered at startup. Add a module under `plugins/builtin/` and register it in the builtin package.
- **Custom:** Uploaded via the Admin UI or API, stored in the database with a `code_path` to the plugin file. Validated and registered in memory on upload.

---

## Plugin interface (BasePlugin)

### Required

- **plugin_type** — Unique string identifier (e.g. `log_event`, `conditional`). Must not conflict with existing plugins.
- **display_name** — Human-readable name shown in the workflow builder.
- **description** — Short description of what the plugin does.
- **plugin_category** — Category used to filter plugins in the workflow builder. Use one of: `campaign`, `sending`, `target_selection`, `email_validation`, `notification`, `data_transform`, `conditional`, `delay`, `capture`, `logging`, or another existing category that fits.
- **config_schema** — JSON Schema-like dictionary defining the plugin’s configuration. Used by the admin UI to render configuration forms.
- **execute(context, config)** — Method that runs the plugin. Receives the execution context and node config; must return an updated context dictionary.

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

Source: [plugins/base.py](plugins/base.py).

### Optional overrides

- **validate_config(config)** — Return a list of error strings for invalid config, or `None` if valid. Called by the workflow engine before `execute`.
- **get_required_context_keys()** — Return a list of context keys the plugin expects. For documentation and tooling.
- **get_provided_context_keys()** — Return a list of context keys the plugin sets. For documentation and tooling.
- **get_branch_context_key()** — Return the context key that holds a boolean (or truthy/falsy) result for branching. If set, the engine follows the "true" or "false" connection from this node.
- **get_branch_labels()** — Return a dict with `"true"` and `"false"` keys for display labels. Default is `{"true": "True", "false": "False"}`.
- **on_error(error, context, config)** — Called when `execute` raises. Return an updated context (e.g. with `_error` set). Default implementation logs and sets `_error` on context.

### Minimal example

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
        # Add custom logic here; always return a dict
        if config.get("message"):
            context = context.copy()
            context["example_message"] = config["message"]
        return context
```

To make it do something useful, add logic in `execute()` and/or extend `config_schema`. Use `interpolate_string()` for config values that may contain `{{ variables }}`.

---

## Execution contract

**Input**

- **context** — Dictionary with at least `campaign`, `request`, `session`, and `variables`. May contain outputs from previous nodes (e.g. `captured_credentials`, `validation_passed`). For sending workflows, context also includes `target`, `targets`, and related keys.
- **config** — The node’s configuration: user-supplied values matching your `config_schema`.

**Output**

- Return a new or mutated context dictionary. Do not return `None`. The engine passes the returned context to the next node or uses it to build the HTTP response.

**Response keys (inbound campaigns)**

To drive the HTTP response, set one or more of these on the returned context:

- **\_response_html** — HTML body.
- **\_response_redirect** — Redirect URL (absolute or relative). Engine uses 302 unless **\_response_status** is set.
- **\_response_json** — JSON-serializable body (engine sends as application/json).
- **\_response_status** — HTTP status code (default 200 for HTML/JSON, 302 for redirect).
- **\_response_headers** — Dict of response headers.

To update the Flask session, set **\_set_session** to a dict of key-value pairs. The executor merges these into the session after the workflow run.

**Branching**

If the plugin has two outcomes (e.g. pass/fail), set a context key to a boolean or truthy/falsy value (e.g. `validation_passed`) and implement `get_branch_context_key()` to return that key name. The workflow engine will follow the "true" or "false" connection from the node based on that value.

---

## Variable interpolation

Config values often contain placeholders such as `{{ request.form_data.email }}` or `{{ campaign.uid }}`. Use the shared helper:

- **interpolate_string(template, context)** in [shared/workflow_variables.py](shared/workflow_variables.py)

Pass the string and the full context; it resolves `{{ variable_name }}` and dot notation for nested keys. Use it whenever you read a config string that may contain placeholders.

---

## Adding a built-in plugin

1. Create a new module under [plugins/builtin/](plugins/builtin/) (e.g. `my_plugin.py`).
2. Implement a class that inherits from `BasePlugin` and defines all required properties and `execute()`.
3. Import the class in [plugins/builtin/__init__.py](plugins/builtin/__init__.py) and add it to `__all__`. The registry discovers subclasses of `BasePlugin` from the `builtin` package on initialization.
4. Run tests: `make test-fast` or `pytest tests/test_plugins/ -v`. Add a test in `tests/test_plugins/` if appropriate.

---

## Adding a custom plugin (upload)

1. Write a single Python file containing one class that inherits from `BasePlugin`. The file must be importable as a module (the registry uses [plugins/registry.py](plugins/registry.py) `load_plugin_from_path()` to load it and registers the first discovered `BasePlugin` subclass).
2. Upload via the Admin UI (Plugins) or the API (e.g. POST with the plugin file). The plugin is validated with `validate_plugin`, then stored in the database with `code_path` and registered in the in-memory registry.
3. Custom plugins remain available until the process restarts. They are not automatically loaded from the database on startup. After a restart, re-upload the plugin or load it from `code_path` if you add that behavior.

---

## Config schema and UI

The admin workflow builder ([admin/templates/workflow_builder.html](admin/templates/workflow_builder.html)) renders the config form from `plugin.config_schema`. The schema follows JSON Schema conventions with Reel-specific extensions.

### Top-level schema

- **type** — Must be `"object"`.
- **properties** — Object mapping property names to property schemas.
- **required** — Array of property names that are required. UI shows a red asterisk and validates emptiness.

### Property metadata (per field)

| Key | Purpose | UI behavior |
|-----|---------|-------------|
| `type` | Data type | Drives input type: `string` (text/textarea), `integer`/`number` (number input), `boolean` (checkbox), `array` (dynamic list), `object` (nested form or JSON textarea). |
| `title` | Field label | Shown as the label. Fallback order: `title` > `description` > property key. |
| `description` | Secondary text | Used as help text when `title` is present; otherwise as label. |
| `help` | Tooltip | Shown on info icon hover. `helpText` is an alias. |
| `placeholder` | Input placeholder | Placeholder text for text inputs. |
| `default` | Default value | Pre-filled when no value exists. Shows "(default)" badge when used. |
| `format` | String hint | For `type: "string"`: `"email"` (email input), `"uri"`/`"url"` (url input), `"textarea"` (multiline textarea). |
| `enum` | Dropdown options | Renders a `<select>`. See enum format below. |
| `properties` | Nested object | For `type: "object"`: renders nested form fields recursively. Without `properties`, falls back to JSON textarea. |
| `items` | Array item schema | For `type: "array"`: defines schema for each item. If `items.properties` exists, each item is a mini-form. |
| `required` | Nested required | For nested objects: array of required nested property names. |

### Enum format

- **Simple:** `["opt1", "opt2"]` — value and label are the same.
- **Object:** `{ "value": <stored>, "label": "Display text", "description": "Tooltip" }` — `value` is stored in config; `label` shown in dropdown; `description` optional.

Example (from [plugins/builtin/redirect.py](plugins/builtin/redirect.py)):

```python
"status_code": {
    "type": "integer",
    "title": "HTTP Status Code",
    "enum": [
        {"value": 302, "label": "302 Found (Temporary)", "description": "Most common for post-action redirects."},
        {"value": 301, "label": "301 Moved Permanently", "description": "Permanent redirect."}
    ],
    "default": 302
}
```

### Nested objects

Use `type: "object"` with `properties` (and optional `required`) for nested fields. The UI renders them in an indented block. Example: [plugins/builtin/conditional.py](plugins/builtin/conditional.py) `condition` object with `type`, `key`, `value`, `regex`.

### Arrays

Use `type: "array"` with `items`. If `items` has `properties`, each array element is rendered as a mini-form with "Add Item" button. Example: [plugins/builtin/validate_input.py](plugins/builtin/validate_input.py) `fields` array with `items.properties` for `name`, `required`, `type`, `min_length`, etc.

### Special options

- **x-optionsSource: "templates"** — Renders a dynamic dropdown populated from `/api/templates`. Used for template selection (e.g. `template_id` in [plugins/builtin/render_template.py](plugins/builtin/render_template.py)).
- **Union types** — `type: ["string", "number"]` is supported; the UI uses the first type for rendering.

### Example schema

```python
"config_schema": {
    "type": "object",
    "properties": {
        "message": {
            "type": "string",
            "title": "Message",
            "description": "Optional message",
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

See [plugins/builtin/conditional.py](plugins/builtin/conditional.py), [plugins/builtin/log_event.py](plugins/builtin/log_event.py), [plugins/builtin/redirect.py](plugins/builtin/redirect.py), and [plugins/builtin/validate_input.py](plugins/builtin/validate_input.py) for more examples.

---

## Testing a plugin

**Unit test**

1. Get the plugin from the registry: `get_registry().get_plugin(plugin_type)` or `get_plugin(plugin_type)`.
2. Build a minimal `context` and `config` dict.
3. Call `plugin.execute(context, config)` and assert on the returned context.

See [tests/test_plugins/test_registry.py](tests/test_plugins/test_registry.py) for a minimal plugin class and [tests/test_plugins/test_builtin_plugins.py](tests/test_plugins/test_builtin_plugins.py) for execution patterns.

**Custom plugin from path**

Use a temporary file containing the plugin code and call `registry.load_plugin_from_path(path)` to load and register it, then run the same execute assertions.

# Reel

Phishing simulation and security awareness framework with configurable workflows, campaign management, and optional credential proxy.

---

## Quickstart (OPS USE)


1. Clone the repository and `cd` into it.
2. run `./deploy.sh` - this will configure the pre-preqs for you, assuming you are on ubuntu.
3. Run `./start.sh --preload-ml`. This will start the servers and pre-load the ML models we use

You get the Admin UI at http://localhost:8000 and the phishing server at http://localhost:1234. Default login: `admin` / `admin123`. Change the password after first login.

Forward 8000 to your local over ssh to access the admin panel. Do NOT expose the admin panel or flask server (port 1234) directly to the internet.

`deploy.sh` will install a caddy server on the same host, everything is build assuming you will use caddy as a reverse proxy. You don't have to, but here be dragons. 

**Optional flags for `start.sh`:**

- `--with-caddy` — Start Caddy via Docker (local testing only).
- `--preload-ml` — Pre-download phishing detection model (~1.3GB); avoids first-use delay when using the Phishing Detector plugin.
- `--reset-db` — Reset the database and reinitialize.
- `--admin-only` — Start only the admin server (port 8000).
- `--phishing-only` — Start only the phishing server (port 1234).
- `--skip-init` — Skip database initialization.
- `--skip-setup` — Skip venv/deps setup; just load `.env` and start servers.

Without `start.sh`, after installing dependencies and initializing the database you can run both servers with: `uv run python -m cli start`.

---

## Dependencies

**Python:** See [requirements.txt](requirements.txt). Core stack includes Flask, SQLAlchemy, Jinja2, Pydantic, Flask-Login, python-jose, passlib, cryptography, and Flask-WTF. The Phishing Detector plugin uses transformers and torch. The credential proxy uses Playwright; optional integrations use OpenAI/Anthropic and boto3 (AWS Connect).

**Development:** [requirements-dev.txt](requirements-dev.txt) adds pytest, pytest-cov, and related testing tools. Install with `uv pip install -r requirements-dev.txt` for running tests and coverage.

**System (production):** The deploy script targets Ubuntu/Debian. It installs uv, Caddy (reverse proxy), and system packages such as libmagic1. For the credential proxy, `start.sh` runs `uv run playwright install chromium` to install Chromium.

---

## Scripts

### deploy.sh

Production preparation on Ubuntu. Idempotent. It:

1. Checks for Ubuntu/Debian.
2. Installs system packages (curl, ca-certificates, libmagic1, etc.).
3. Installs uv if missing.
4. Installs Caddy as the reverse proxy (APT).
5. Creates a virtual environment and installs Python dependencies from `requirements.txt`.
6. Creates `.env` from `.env.example` if missing and generates `SECRET_KEY` and `JWT_SECRET_KEY` if not set.
7. Creates required directories (`storage/caddy/data`, `storage/caddy/config`, `storage/uploads`, `storage/templates`, `storage/assets`, `instance`).
8. Optionally initializes the database with `--init-db` (runs `uv run python -m cli init --force`).

It does not start the application. For production: start Caddy (reverse proxy), then start the app with `./start.sh` or a process manager so all traffic reaches the app via the proxy.

### start.sh

Development and local startup. It:

1. Loads `.env` and ensures `SECRET_KEY` and `JWT_SECRET_KEY` (generates them if default values are present).
2. Verifies uv is installed.
3. Creates a virtual environment if missing.
4. Installs dependencies from `requirements.txt`.
5. Runs `uv run playwright install chromium` for the credential proxy.
6. Optionally pre-downloads the phishing detection model with `--preload-ml` (~1.3GB).
7. Initializes the database if no database file exists (unless `--skip-init`). Use `--reset-db` to delete and reinitialize.
8. Optionally starts Caddy via Docker with `--with-caddy` (local testing only).
9. Starts servers: by default both admin and phishing via `uv run python -m cli start`; use `--admin-only` or `--phishing-only` to run one.

---

## Production deployment

In production, the application **must** run behind a reverse proxy. Do not expose the Flask development servers directly to the internet.

The reverse proxy is responsible for TLS termination, correct Host headers, path and domain routing, and separating admin traffic from campaign traffic. The app listens on localhost or an internal port; the proxy handles public HTTPS and forwards to the admin server (e.g. port 8000) and the phishing server (e.g. port 1234) according to your configuration.

**Recommended:** Use Caddy as the reverse proxy. `deploy.sh` installs Caddy via APT. The project includes Caddyfile examples (e.g. [Caddyfile.minimal](Caddyfile.minimal)). After running `deploy.sh`, start Caddy (e.g. `caddy run --config /path/to/Caddyfile.minimal`) and then start the application with `./start.sh` or a process manager. Any equivalent reverse proxy (nginx, Traefik, etc.) is acceptable as long as the app is not exposed directly.

---

## Project intent

Reel is a phishing simulation and security awareness framework. Operators use the **admin UI** to manage campaigns, workflows, templates, and targets. The **phishing server** serves campaign landing pages and runs **workflows**—node-based graphs of plugins—on each request.

There are two application entry points in [app.py](app.py): `create_app()` for the phishing server and `create_admin_app()` for the admin UI. Campaigns can be **inbound** (a visitor follows a link; GET and POST workflows handle page views and form submissions) or **outbound** (the system sends emails or calls via sending workflows). Caddy can be used for domain-based routing of campaigns. The credential proxy uses Playwright for browser automation to replay captured credentials on target sites.

---

## Workflow concepts

### Inbound

A user visits a campaign URL (e.g. `/<campaign_uid>`). The phishing server routes by campaign UID. For GET requests it runs the campaign’s **GET workflow** (e.g. render landing page, CAPTCHA); for POST requests it runs the **POST workflow** (e.g. validate input, capture credentials, redirect). Workflows are of type **campaign** and declare HTTP method support (GET, POST, or BOTH). Execution context includes `campaign`, `request`, `session`, and `variables`. The response is taken from context keys such as `_response_html`, `_response_redirect`, or `_response_json`. Inbound workflows are used for landing pages, CAPTCHA, credential capture, redirects, and logging.

### Outbound

An operator runs a **sending workflow** from the admin UI, linked to a campaign (the campaign’s “Workflow” / sending workflow). The sending executor runs a single workflow of type **sending**: it selects targets (e.g. from CSV or tracked users), optionally validates or pre-renders content, then iterates over targets—rendering the email, applying rate limits, and sending via a plugin (e.g. SMTP). There is no visitor GET/POST; the workflow generates content and sends it to a list of targets.

**Summary:**

- **Inbound:** Visitor-driven. GET and POST requests trigger campaign workflows. Used for landing pages and form handling.
- **Outbound:** Operator-driven. A single sending workflow runs in the admin context and batch-sends to targets (email, voice, etc.).

---

## Workflow Variables

When building workflows, use `{{variable}}` syntax for interpolation. Nested paths use dot notation: `{{nested.key}}`.

### Target Data (from CSV / Loop)

| Variable | Description |
|----------|-------------|
| `{{target}}` | Full target object for the current loop iteration |
| `{{target.email}}` | Target email |
| `{{target.first_name}}` | First name |
| `{{target.last_name}}` | Last name |
| `{{target.custom_data}}` | Dict of other CSV columns |
| `{{target.custom_data.column_name}}` | Any extra CSV column (e.g. `{{target.custom_data.company}}`, `{{target.custom_data.landing_page}}`) |
| `{{target.name}}` | Shorthand for `first_name` or `target.first_name` |
| `{{target_name}}` | Same as `target.name` (alias) |
| `{{target_email}}` | Same as `target.email` (alias) |
| `{{_loop_index}}` | Current loop index (0-based) |

**CSV example:** `email,first_name,last_name,company,landing_page` → use `{{target.email}}`, `{{target.first_name}}`, `{{target.company}}`, `{{target.custom_data.landing_page}}`.

### Campaign / Config

| Variable | Description |
|----------|-------------|
| `{{campaign.id}}` | Campaign ID |
| `{{campaign.uid}}` | Campaign UID |
| `{{campaign.name}}` | Campaign name |
| `{{campaign.template_html}}` | Campaign template HTML |
| `{{url}}` | Campaign landing URL |
| `{{campaign_id}}` | Campaign ID |
| `{{variables}}` | Campaign variables dict |

### URLs

| Variable | Description |
|----------|-------------|
| `{{url}}` | Campaign landing URL (set by config or default) |
| `{{target.landing_page}}` | If `landing_page` exists in CSV |
| `{{target.custom_data.landing_page}}` | Same as above when `landing_page` is in custom_data |
| `{{target.ip}}` | If `ip` is in CSV or custom data |

**URL Obfuscator:** Use `url` or `target.landing_page` as the source, or interpolate: `http://{{target.ip}}/login`.

### HTML / BERT Phishing Detector

| Variable | Description |
|----------|-------------|
| `{{template_html}}` | Rendered HTML |
| `{{campaign.template_html}}` | Campaign template HTML |
| `{{email_html}}` | Rendered email HTML (after Render Template) |
| `{{body_html}}` | Email body HTML |

**Phishing Detector:** Set `html_content` to `{{template_html}}`, `{{campaign.template_html}}`, or `{{email_html}}`.

### Plugin Outputs

| Variable | Description |
|----------|-------------|
| `{{phishing_detection.is_phishing}}` | True/False from BERT |
| `{{phishing_detection.confidence}}` | BERT confidence score |
| `{{captured_credentials.username}}` | Inbound only |
| `{{captured_credentials.password}}` | Inbound only |
| `{{_email_sent}}` | Indicates SMTP send success |

### Simple Sending Workflow Example

```
Target Selector (CSV) → Loop (array_source: targets, item_key: target) → Render Template → SMTP Sender
```

Typical tags in the template: `{{target.email}}`, `{{target.first_name}}`, `{{target.last_name}}`, `{{target.custom_data.X}}` for any extra CSV column.

---

## Plugins

Workflows are built from nodes; each node is a plugin with configuration. The following built-in plugins are available.

| Plugin | Purpose |
|--------|---------|
| **CAPTCHA** | Cloudflare Turnstile: validate tokens and/or render widget; protect forms from bots. |
| **Capture Credentials** | Capture credentials from form submissions; store in context and DB for downstream plugins. |
| **Conditional Logic** | Branch workflow on True/False using context (equality, containment, numeric, regex). |
| **Data Transform** | Set, remove, copy, rename, merge, or filter context data for downstream plugins. |
| **Delay** | Fixed or random delay, or delay until a datetime; rate limiting and timing. |
| **Email Template Validator** | Validate templates (Jinja2, quality, spam); optional AI; branch on result. |
| **Generate Device Code (GraphSpy)** | Azure AD device codes via GraphSpy API; use with AWS Connect for voice delivery. |
| **AWS Connect Dialer** | Outbound voice via AWS Connect; SSML; integrates with GraphSpy for device codes. |
| **Log Event** | Log custom events to the database; request/session data; audit and analytics. |
| **Phishing Detector (BERT)** | ML-based phishing detection on HTML; QA and content analysis. |
| **Pushover** | Push notifications (iOS, Android, desktop) via Pushover API. |
| **Queue Credential Proxy** | After Capture Credentials, queue a browser-automation job to replay on target sites. |
| **Redirect** | HTTP redirect to a URL with configurable status code; variable interpolation. |
| **Render Template** | Render HTML from campaign, custom, or library template with Jinja2 and variables. |
| **Send Slack Message** | Send a message to Slack via webhook or bot; variable interpolation. |
| **SMTP Email Sender** | Send email via SMTP (TLS, auth, HTML/plain text, variables); used in sending workflows. |
| **Target Selector** | Select targets from CSV, manual list, or tracked users; filter by domain/count; feed sending workflows. |
| **URL Obfuscator** | Obfuscate IPs/URLs (e.g. DWORD, hex, IPv6-mapped); for testing and research. |
| **User Agent Check** | Allow or block by user-agent regex; block, redirect, or branch on result. |
| **Validate Input** | Validate form fields (required, type, length, regex); block, redirect, or continue. |

---

## Development

- Run tests: `make test-fast` or `./run_tests.sh`
- Tests with coverage: `make test-coverage` or `./run_tests.sh --coverage`
- Lint: `make lint`
- Format check: `make format-check`

See the [Makefile](Makefile) for additional targets (unit/integration/functional test splits, DB init/reset, run admin or phishing server only).

---

## Documentation

- [DEVELOPMENT.md](DEVELOPMENT.md) — Detailed instructions for developing custom plugins.
- [AGENT_README.md](AGENT_README.md) — For AI agents: codebase concepts, plugin system, style guides, and prompts.

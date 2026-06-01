#!/usr/bin/env python3
"""Minimal login + MFA app - run on localhost as sync credential proxy target.

MFA flows available:

  /login                  - OTP flow: after login, asks for a 6-digit code (printed to console)
  /login-push             - Number-matching flow: after login, displays a 2-digit number and
                            waits for approval. Call /approve/<token> to simulate the auth app.
  /login-multistep        - Multi-step flow: email-only page → password-only page → MFA page.
                            Tests credential proxy handling of separate email/password pages.
  /login-passwordless-push - Fully passwordless: email-only page → push approval (no password
                             at any step). Call /approve/<token> to simulate.
  /login-passkey          - Email-only page → "Verify with passkey" button (no input field).
                            Tests how the proxy handles WebAuthn-style prompts.

Usage: python credproxy_mfa_test.py
Then add http://127.0.0.1:5001/<flow> as the target URL in Sync Credential Proxy.
"""
from flask import Flask, request, render_template_string, make_response, session, jsonify
import secrets
import random

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

# In-memory store for pending push approvals: token -> {"approved": bool, "email": str}
_pending_pushes = {}

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head><title>Login (MFA target)</title></head>
<body>
  <h1>Login</h1>
  {% if error %}<p style="color:red">{{ error }}</p>{% endif %}
  <form method="POST" action="{{ action }}">
    <label>Email <input type="text" name="email" /></label><br/>
    <label>Password <input type="password" name="password" /></label><br/>
    <button type="submit">Sign in</button>
  </form>
</body>
</html>
"""

MFA_HTML = """
<!DOCTYPE html>
<html>
<head><title>Verification Required</title></head>
<body>
  <h1>Verification Required</h1>
  <p>Enter the 6-digit code from your authenticator app.</p>
  {% if error %}<p style="color:red">{{ error }}</p>{% endif %}
  <form method="POST" action="/mfa">
    <label>Code <input type="text" name="mfa_code" autocomplete="one-time-code" /></label><br/>
    <button type="submit">Verify</button>
  </form>
</body>
</html>
"""

PUSH_HTML = """
<!DOCTYPE html>
<html>
<head><title>Approve Sign-in</title></head>
<body>
  <h1>Approve the sign-in request</h1>
  <p>Open your authenticator app and enter the number shown below.</p>
  <p style="font-size:3em;font-weight:bold;text-align:center;margin:24px 0">{{ number }}</p>
  <p id="status">Waiting for approval...</p>
  <script>
    var poll = setInterval(function() {
      fetch("/push-status/{{ token }}")
        .then(function(r) { return r.json(); })
        .then(function(data) {
          if (data.approved) {
            clearInterval(poll);
            document.getElementById("status").textContent = "Approved! Redirecting...";
            window.location.href = "/push-complete/{{ token }}";
          }
        });
    }, 2000);
  </script>
</body>
</html>
"""

PASSWORDLESS_EMAIL_HTML = """
<!DOCTYPE html>
<html>
<head><title>Sign in</title></head>
<body>
  <h1>Sign in</h1>
  <p>Enter your email. We'll send a push notification to your authenticator.</p>
  {% if error %}<p style="color:red">{{ error }}</p>{% endif %}
  <form method="POST" action="{{ action }}">
    <label>Email <input type="email" name="email" autocomplete="username" /></label><br/>
    <button type="submit">Next</button>
  </form>
</body>
</html>
"""

PASSKEY_PROMPT_HTML = """
<!DOCTYPE html>
<html>
<head><title>Verify with passkey</title></head>
<body>
  <h1>Verify with passkey</h1>
  <p>Use your passkey to sign in as {{ email }}.</p>
  <button type="button" id="passkey-btn">Verify with passkey</button>
  <p style="color:#888;font-size:0.9em">(This is a mock — no actual WebAuthn ceremony happens.)</p>
</body>
</html>
"""

MULTISTEP_EMAIL_HTML = """
<!DOCTYPE html>
<html>
<head><title>Sign in - Email</title></head>
<body>
  <h1>Sign in</h1>
  <p>Enter your email to continue.</p>
  {% if error %}<p style="color:red">{{ error }}</p>{% endif %}
  <form method="POST" action="/login-multistep">
    <label>Email <input type="email" name="email" /></label><br/>
    <button type="submit">Next</button>
  </form>
</body>
</html>
"""

MULTISTEP_PASSWORD_HTML = """
<!DOCTYPE html>
<html>
<head><title>Sign in - Password</title></head>
<body>
  <h1>Welcome back, {{ email }}</h1>
  <p>Enter your password.</p>
  {% if error %}<p style="color:red">{{ error }}</p>{% endif %}
  <form method="POST" action="/login-multistep-password">
    <label>Password <input type="password" name="password" /></label><br/>
    <button type="submit">Sign in</button>
  </form>
</body>
</html>
"""

SUCCESS_HTML = """
<!DOCTYPE html>
<html>
<head><title>Dashboard</title></head>
<body>
  <h1 class="dashboard">Welcome, {{ email }}!</h1>
  <p>Login + MFA successful. Cookies were set.</p>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template_string(LOGIN_HTML, error=None, action="/login")


def _make_success_response(email):
    resp = make_response(render_template_string(SUCCESS_HTML, email=email))
    resp.set_cookie("session_id", secrets.token_urlsafe(32), max_age=3600, httponly=True, samesite="Lax")
    resp.set_cookie("logged_in", "true", max_age=3600)
    resp.set_cookie("user_email", email[:64], max_age=3600)
    return resp

# ---------------------------------------------------------------------------
# Flow 1: OTP code entry   (target_url = /login)
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "")
        password = request.form.get("password", "")
        print(f"[MFA TARGET] Login submitted: email={email}")

        if not email or not password:
            return render_template_string(LOGIN_HTML, error="Email and password required", action="/login")

        code = f"{random.randint(0, 999999):06d}"
        session["mfa_code"] = code
        session["email"] = email
        print(f"[MFA TARGET] MFA code for {email}: {code}  <-- enter this on the MFA page")

        return render_template_string(MFA_HTML, error=None)

    return render_template_string(LOGIN_HTML, error=None, action="/login")


@app.route("/mfa", methods=["GET", "POST"])
def mfa():
    if request.method == "POST":
        submitted = request.form.get("mfa_code", "").strip()
        expected = session.get("mfa_code", "")
        email = session.get("email", "unknown")
        print(f"[MFA TARGET] MFA submitted: {submitted} (expected {expected})")

        if submitted == expected:
            session.pop("mfa_code", None)
            return _make_success_response(email)

        return render_template_string(MFA_HTML, error="Invalid code. Try again.")

    if not session.get("mfa_code"):
        return render_template_string(LOGIN_HTML, error=None, action="/login")
    return render_template_string(MFA_HTML, error=None)

# ---------------------------------------------------------------------------
# Flow 2: Number-matching / push approval   (target_url = /login-push)
# ---------------------------------------------------------------------------

@app.route("/login-push", methods=["GET", "POST"])
def login_push():
    if request.method == "POST":
        email = request.form.get("email", "")
        password = request.form.get("password", "")
        print(f"[PUSH TARGET] Login submitted: email={email}")

        if not email or not password:
            return render_template_string(LOGIN_HTML, error="Email and password required", action="/login-push")

        # Generate a 2-digit number and an approval token
        number = random.randint(10, 99)
        token = secrets.token_urlsafe(16)
        _pending_pushes[token] = {"approved": False, "email": email}
        session["push_token"] = token

        print(f"[PUSH TARGET] Number-match for {email}: {number}")
        print(f"[PUSH TARGET] Approve URL: http://127.0.0.1:5001/approve/{token}")

        return render_template_string(PUSH_HTML, number=number, token=token)

    return render_template_string(LOGIN_HTML, error=None, action="/login-push")


@app.route("/push-status/<token>")
def push_status(token):
    """Polling endpoint — browser JS checks this every 2 s."""
    entry = _pending_pushes.get(token)
    if not entry:
        return jsonify({"approved": False, "error": "unknown token"})
    return jsonify({"approved": entry["approved"]})


@app.route("/approve/<token>")
def approve(token):
    """Simulate the authenticator app approving the push. Visit this URL manually."""
    entry = _pending_pushes.get(token)
    if not entry:
        return "Unknown token", 404
    entry["approved"] = True
    print(f"[PUSH TARGET] Approved push for {entry['email']}")
    return f"Approved! {entry['email']} can now sign in."


@app.route("/push-complete/<token>")
def push_complete(token):
    """After approval, JS redirects here to set cookies and show dashboard."""
    entry = _pending_pushes.pop(token, None)
    if not entry or not entry["approved"]:
        return render_template_string(LOGIN_HTML, error="Approval expired or invalid", action="/login-push")
    return _make_success_response(entry["email"])

# ---------------------------------------------------------------------------
# Flow 3: Multi-step login   (target_url = /login-multistep)
#   Step 1: email-only form
#   Step 2: password-only form
#   Step 3: MFA code page (same OTP flow as /login)
# ---------------------------------------------------------------------------

@app.route("/login-multistep", methods=["GET", "POST"])
def login_multistep():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        print(f"[MULTISTEP TARGET] Email submitted: {email}")

        if not email:
            return render_template_string(MULTISTEP_EMAIL_HTML, error="Email is required")

        session["multistep_email"] = email
        return render_template_string(MULTISTEP_PASSWORD_HTML, email=email, error=None)

    return render_template_string(MULTISTEP_EMAIL_HTML, error=None)


@app.route("/login-multistep-password", methods=["POST"])
def login_multistep_password():
    email = session.get("multistep_email", "")
    if not email:
        return render_template_string(MULTISTEP_EMAIL_HTML, error="Session expired, start over")

    password = request.form.get("password", "").strip()
    print(f"[MULTISTEP TARGET] Password submitted for {email}")

    if not password:
        return render_template_string(MULTISTEP_PASSWORD_HTML, email=email, error="Password is required")

    # Password accepted — generate MFA code (same as OTP flow)
    code = f"{random.randint(0, 999999):06d}"
    session["mfa_code"] = code
    session["email"] = email
    print(f"[MULTISTEP TARGET] MFA code for {email}: {code}  <-- enter this on the MFA page")

    return render_template_string(MFA_HTML, error=None)


# ---------------------------------------------------------------------------
# Flow 4: Passwordless push   (target_url = /login-passwordless-push)
#   Step 1: email-only form (no password field anywhere)
#   Step 2: number-matching push approval — same as /login-push but without the
#           password leg before it
# ---------------------------------------------------------------------------

@app.route("/login-passwordless-push", methods=["GET", "POST"])
def login_passwordless_push():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        print(f"[PASSWORDLESS PUSH TARGET] Email submitted: {email}")

        if not email:
            return render_template_string(
                PASSWORDLESS_EMAIL_HTML, error="Email is required",
                action="/login-passwordless-push",
            )

        number = random.randint(10, 99)
        token = secrets.token_urlsafe(16)
        _pending_pushes[token] = {"approved": False, "email": email}
        session["push_token"] = token

        print(f"[PASSWORDLESS PUSH TARGET] Number-match for {email}: {number}")
        print(f"[PASSWORDLESS PUSH TARGET] Approve URL: http://127.0.0.1:5001/approve/{token}")

        return render_template_string(PUSH_HTML, number=number, token=token)

    return render_template_string(
        PASSWORDLESS_EMAIL_HTML, error=None,
        action="/login-passwordless-push",
    )

# ---------------------------------------------------------------------------
# Flow 5: Passkey-only   (target_url = /login-passkey)
#   Step 1: email-only form
#   Step 2: passkey prompt page — no input fields, just a button. Sits there.
#   Used to verify the proxy doesn't return success on this state.
# ---------------------------------------------------------------------------

@app.route("/login-passkey", methods=["GET", "POST"])
def login_passkey():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        print(f"[PASSKEY TARGET] Email submitted: {email}")

        if not email:
            return render_template_string(
                PASSWORDLESS_EMAIL_HTML, error="Email is required",
                action="/login-passkey",
            )

        return render_template_string(PASSKEY_PROMPT_HTML, email=email)

    return render_template_string(
        PASSWORDLESS_EMAIL_HTML, error=None, action="/login-passkey",
    )


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("MFA proxy target app running on http://127.0.0.1:5001")
    print()
    print("  OTP flow:                  http://127.0.0.1:5001/login")
    print("  Number-match flow:         http://127.0.0.1:5001/login-push")
    print("  Multi-step flow:           http://127.0.0.1:5001/login-multistep")
    print("  Passwordless push flow:    http://127.0.0.1:5001/login-passwordless-push")
    print("  Passkey flow:              http://127.0.0.1:5001/login-passkey")
    print()
    print("For push flows, after email submission the approve URL is printed here.")
    app.run(host="127.0.0.1", port=5001, debug=True)

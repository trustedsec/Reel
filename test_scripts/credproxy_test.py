#!/usr/bin/env python3
"""Minimal login form app - run on localhost as credential proxy target.
   Sets cookies on successful login so you can test credential proxy cookie capture.
   Usage: python proxy_target_app.py
   Then add http://127.0.0.1:5000/ or http://127.0.0.1:5000/login as a target site in Queue Credential Proxy.
"""
from flask import Flask, request, render_template_string, make_response
import secrets

app = Flask(__name__)

LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head><title>Login (proxy target)</title></head>
<body>
  <h1>Login</h1>
  <form method="POST" action="/login">
    <label>Email <input type="text" name="email" /></label><br/>
    <label>Password <input type="password" name="password" /></label><br/>
    <button type="submit">Sign in</button>
  </form>
</body>
</html>
"""

SUCCESS_HTML = """
<!DOCTYPE html>
<html>
<head><title>OK</title></head>
<body><h1>Login received</h1><p>Check the server console for submitted data. Cookies were set.</p></body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(LOGIN_HTML)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        data = dict(request.form)
        print("[PROXY TARGET] Form submitted:", data)
        resp = make_response(render_template_string(SUCCESS_HTML))
        # Set cookies so credential proxy can capture them
        resp.set_cookie("session_id", secrets.token_urlsafe(32), max_age=3600, httponly=True, samesite="Lax")
        resp.set_cookie("logged_in", "true", max_age=3600)
        resp.set_cookie("user_email", data.get("email", "")[:64], max_age=3600)  # truncate for safety
        return resp
    return render_template_string(LOGIN_HTML)

if __name__ == "__main__":
    print("Proxy target app: http://127.0.0.1:5000/ or http://127.0.0.1:5000/login")
    app.run(host="127.0.0.1", port=5000, debug=True)
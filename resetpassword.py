#!/usr/bin/env python3
"""Reset admin password to default (admin123)."""

from app import create_app
from shared.database import db, User

app = create_app()
with app.app_context():
    user = User.query.filter_by(username='admin').first()
    if not user:
        print("No admin user found.")
        exit(1)

    user.set_password('admin123')
    user.password_reset_required = True
    user.failed_login_attempts = 0
    user.locked_until = None
    db.session.commit()
    print("Admin password reset to default. You'll be prompted to change it on login.")

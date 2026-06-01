"""
CLI entry point for Reel v2
Usage: python -m cli [command] [options]
"""
import sys
import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

@click.group()
@click.version_option(version='2.0.0', prog_name='Reel')
def cli():
    """Reel v2 - Modern Phishing Framework CLI"""
    pass

@cli.command()
@click.option('--force', is_flag=True, help='Force initialization even if database exists')
def init(force):
    """Initialize Reel v2 database and create default user"""
    from cli.setup import initialize_database
    initialize_database(force)

@cli.command()
def create_admin():
    """Create default admin user"""
    try:
        import sys
        import os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        
        from app import create_admin_app
        from shared.auth import create_default_user
        
        app = create_admin_app()
        with app.app_context():
            create_default_user()
            
    except Exception as e:
        console.print(f"[red]Failed to create admin user: {e}[/red]")

@cli.command()
@click.option('--admin-host', default=None, help='Admin host to bind to')
@click.option('--admin-port', default=None, type=int, help='Admin port to bind to')
@click.option('--phishing-host', default=None, help='Phishing host to bind to')
@click.option('--phishing-port', default=None, type=int, help='Phishing port to bind to')
@click.option('--debug', is_flag=True, help='Enable debug mode')
def start(admin_host, admin_port, phishing_host, phishing_port, debug):
    """Start both admin and phishing servers"""
    import threading
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    
    from app import create_app, create_admin_app
    from shared.config import Config
    
    # Use config defaults if not specified
    config = Config()
    admin_host = admin_host or config.ADMIN_HOST
    admin_port = admin_port or config.ADMIN_PORT
    phishing_host = phishing_host or config.PHISHING_HOST
    phishing_port = phishing_port or config.PHISHING_PORT
    
    # Start phishing server in background thread
    phishing_app = create_app()
    phishing_thread = threading.Thread(
        target=lambda: phishing_app.run(host=phishing_host, port=phishing_port, debug=False)
    )
    phishing_thread.daemon = True
    phishing_thread.start()
    
    console.print(f"🎣 Phishing server started on http://{phishing_host}:{phishing_port}")
    console.print(f"🔧 Admin server starting on http://{admin_host}:{admin_port}")
    console.print("Press Ctrl+C to stop both servers")
    
    try:
        # Start admin server in main thread
        admin_app = create_admin_app()
        admin_app.run(host=admin_host, port=admin_port, debug=debug)
    except KeyboardInterrupt:
        console.print("\n[yellow]Servers stopped by user[/yellow]")

@cli.command()
def status():
    """Check server status"""
    console.print(Panel.fit(
        "🔍 Checking Reel v2 Status",
        border_style="blue"
    ))
    
    from cli.setup import check_database_status
    check_database_status()

@cli.command()
@click.argument('username')
@click.argument('email')
@click.option('--password', prompt=True, hide_input=True, help='User password')
@click.option('--admin', is_flag=True, help='Make user an administrator')
def create_user(username, email, password, admin):
    """Create a new user"""
    try:
        import sys
        import os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        
        from app import create_app
        from shared.database import db, User
        
        app = create_app()
        with app.app_context():
            # Check if user already exists
            if User.query.filter_by(username=username).first():
                console.print(f"[red]Error: Username '{username}' already exists[/red]")
                return
            
            if User.query.filter_by(email=email).first():
                console.print(f"[red]Error: Email '{email}' already exists[/red]")
                return
            
            # Create user
            user = User(
                username=username,
                email=email,
                is_admin=admin,
                is_active=True
            )
            user.set_password(password)
            
            db.session.add(user)
            db.session.commit()
            
            console.print(f"[green]✅ User '{username}' created successfully[/green]")
            if admin:
                console.print("[yellow]User has administrator privileges[/yellow]")
                
    except Exception as e:
        console.print(f"[red]Failed to create user: {e}[/red]")

@cli.command()
def list_campaigns():
    """List all campaigns"""
    try:
        import sys
        import os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        
        from app import create_app
        from shared.database import Campaign
        from rich.table import Table
        
        app = create_app()
        with app.app_context():
            campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
            
            if not campaigns:
                console.print("[yellow]No campaigns found[/yellow]")
                return
            
            table = Table(title="Campaigns")
            table.add_column("ID", style="cyan")
            table.add_column("Name", style="white")
            table.add_column("Type", style="yellow")
            table.add_column("Status", style="green")
            table.add_column("UID", style="blue")
            table.add_column("Created", style="dim")
            
            for campaign in campaigns:
                workflows = []
                if campaign.get_workflow:
                    workflows.append(f"GET:{campaign.get_workflow.name}")
                if campaign.post_workflow:
                    workflows.append(f"POST:{campaign.post_workflow.name}")
                workflow_str = ", ".join(workflows) if workflows else "None"
                table.add_row(
                    str(campaign.id),
                    campaign.name,
                    workflow_str,
                    campaign.status,
                    campaign.uid,
                    campaign.created_at.strftime('%Y-%m-%d %H:%M')
                )
            
            console.print(table)
            
    except Exception as e:
        console.print(f"[red]Failed to list campaigns: {e}[/red]")

@cli.command()
def version():
    """Show version information"""
    console.print(Panel.fit(
        Text("Reel v2.0.0\nModern Phishing Framework", justify="center"),
        title="Version Info",
        border_style="blue"
    ))

if __name__ == '__main__':
    try:
        cli()
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)

"""
Database setup and initialization
"""
import os
import sys
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

console = Console()

def initialize_database(force=False):
    """Initialize the database and create default user"""
    
    console.print(Panel.fit(
        "🔧 Initializing Reel v2 Database",
        border_style="blue"
    ))
    
    # Check if database already exists (check both locations)
    db_path = Path("reel.db")
    instance_db_path = Path("instance/reel.db")
    
    # Use instance path if it exists, otherwise use root
    if instance_db_path.exists():
        db_path = instance_db_path
    elif not db_path.exists():
        # Default to instance directory
        db_path = instance_db_path
        # Ensure instance directory exists
        db_path.parent.mkdir(exist_ok=True)
    
    if db_path.exists() and not force:
        if not Confirm.ask("Database already exists. Do you want to reinitialize it?"):
            console.print("[yellow]Database initialization cancelled[/yellow]")
            return
    
    try:
        # Add project root to Python path
        project_root = Path(__file__).parent.parent
        sys.path.insert(0, str(project_root))
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            
            # Import after adding to path
            task1 = progress.add_task("Creating database tables...", total=None)
            
            from app import create_app
            from shared.database import db
            from shared.auth import create_default_user
            from shared.config import Config
            
            app = create_app()
            
            # Get the actual database path from app config
            db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', app.config.get('DATABASE_URL', 'sqlite:///reel.db'))
            # Extract path from SQLite URI (sqlite:///path or sqlite:////absolute/path)
            if db_uri.startswith('sqlite:///'):
                actual_db_path = db_uri.replace('sqlite:///', '')
                if actual_db_path.startswith('/'):
                    # Absolute path
                    actual_db_path = Path(actual_db_path)
                else:
                    # Relative path - resolve relative to current working directory
                    actual_db_path = Path.cwd() / actual_db_path
                # Update db_path to match actual location
                if actual_db_path.exists() or not db_path.exists():
                    db_path = actual_db_path
            
            with app.app_context():
                # Delete database file if force (cleaner than dropping tables)
                # Check all possible database file locations
                possible_db_files = [db_path, Path("reel.db"), Path("instance/reel.db"), Path.cwd() / "reel.db", Path.cwd() / "instance" / "reel.db"]
                if force:
                    progress.update(task1, description="Removing existing database...")
                    # Close all database connections and dispose of the engine
                    db.session.close()
                    if hasattr(db, 'engine'):
                        db.engine.dispose()# Delete all possible database files
                    for check_path in possible_db_files:
                        abs_check = check_path.resolve() if (check_path.is_absolute() or check_path.exists()) else (Path.cwd() / check_path)
                        if abs_check.exists():
                            try:
                                abs_check.chmod(0o666)  # Make writable
                                abs_check.unlink()
                            except (OSError, PermissionError) as e:
                                pass  # Ignore permission errors
                    # Dispose of engine to clear stale connections, but don't reinitialize
                    # The app already has db initialized, we just need to clear the connection pool
                    if hasattr(db, 'engine') and db.engine:
                        db.engine.dispose()
                    # Close any open sessions
                    db.session.close()
                
                # Ensure instance directory exists and is writable
                if db_path.parent != Path("."):
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        db_path.parent.chmod(0o755)  # Ensure directory is writable
                    except (OSError, PermissionError):
                        pass  # Ignore permission errors
                
                # Also ensure current directory is writable (for sqlite:///reel.db)
                try:
                    Path(".").chmod(0o755)
                except (OSError, PermissionError):
                    pass  # Ignore permission errors
                
                # Create tables
                progress.update(task1, description="Creating database tables...")
                db.create_all()
                
                # Commit to ensure database file is created
                db.session.commit()
                
                # Ensure database file is writable after creation (use actual file location if found)
                files_to_fix = [db_path] if db_path.exists() else []
                # Also check common locations
                possible_paths = [Path.cwd() / "reel.db", Path("reel.db"), Path("instance/reel.db")]
                for check_path in possible_paths:
                    abs_check = check_path.resolve() if check_path.is_absolute() or check_path.exists() else Path.cwd() / check_path
                    if abs_check.exists() and abs_check not in files_to_fix:
                        files_to_fix.append(abs_check)
                
                for check_path in files_to_fix:
                    try:
                        # Make file writable
                        import os
                        os.chmod(str(check_path), 0o666)  # More permissive
                        # Also ensure parent directory is writable
                        if check_path.parent.exists():
                            os.chmod(str(check_path.parent), 0o777)  # More permissive
                    except (OSError, PermissionError) as e:
                        console.print(f"[yellow]Warning: Could not set permissions on {check_path}: {e}[/yellow]")
                
                # Initialize configuration
                progress.update(task1, description="Initializing configuration...")
                Config.init_app(app)
                
                # Create default user
                progress.update(task1, description="Creating default admin user...")
                try:
                    create_default_user()
                    db.session.commit()  # Ensure user is saved
                except Exception as e:
                    # If user creation fails due to permissions, try to fix and retry
                    if "readonly" in str(e).lower():
                        console.print("[yellow]Database appears read-only, attempting to fix permissions...[/yellow]")
                        # Rollback the failed transaction first
                        db.session.rollback()
                        possible_paths = [db_path, Path("reel.db"), Path("instance/reel.db"), Path.cwd() / "reel.db", Path.cwd() / "instance" / "reel.db"]
                        for check_path in possible_paths:
                            if check_path.exists():
                                try:
                                    import os
                                    os.chmod(str(check_path), 0o666)  # More permissive
                                    os.chmod(str(check_path.parent), 0o777)  # More permissive
                                except Exception as perm_err:
                                    pass
                        # Close session and check for SQLite journal files that might be read-only
                        db.session.close()
                        # Fix permissions on journal files too
                        import glob
                        for journal_file in glob.glob('*.db-journal') + glob.glob('*.db-wal') + glob.glob('*.db-shm') + glob.glob('instance/*.db-journal') + glob.glob('instance/*.db-wal') + glob.glob('instance/*.db-shm'):
                            try:
                                os.chmod(journal_file, 0o666)
                            except:
                                pass
                        # Retry user creation
                        create_default_user()
                        db.session.commit()
                    else:
                        raise
                
                progress.update(task1, description="Database initialized successfully!")
        
        # Show success message
        success_panel = Panel.fit(
            """✅ Database initialized successfully!

Default Admin User:
• Username: admin
• Password: admin123

⚠️ Please change the default password immediately!

Next steps:
1. Copy .env.example to .env and configure settings
2. Run: python -m cli start
3. Visit: http://localhost:8000/admin""",
            title="Setup Complete",
            border_style="green"
        )
        
        console.print(success_panel)
        
    except Exception as e:
        console.print(f"[red]❌ Database initialization failed: {e}[/red]")
        if force:
            console.print("[yellow]Try running without --force flag[/yellow]")
        raise

def check_database_status():
    """Check database status and show information"""
    
    # Check both possible database locations
    db_path = Path("instance/reel.db")
    if not db_path.exists():
        db_path = Path("reel.db")
    
    if not db_path.exists():
        console.print("[red]❌ Database not found[/red]")
        console.print("Run: [cyan]python -m cli init[/cyan] to initialize")
        return False
    
    try:
        # Add project root to Python path
        project_root = Path(__file__).parent.parent
        sys.path.insert(0, str(project_root))
        
        from app import create_app
        from shared.database import db, User, Campaign, Template, Asset
        
        app = create_app()
        
        with app.app_context():
            # Get database statistics
            stats = Table(title="Database Statistics")
            stats.add_column("Table", style="cyan")
            stats.add_column("Count", style="green")
            
            stats.add_row("Users", str(User.query.count()))
            stats.add_row("Campaigns", str(Campaign.query.count()))
            stats.add_row("Templates", str(Template.query.count()))
            stats.add_row("Assets", str(Asset.query.count()))
            
            console.print(stats)
            
            # Show recent activity
            recent_campaigns = Campaign.query.order_by(Campaign.created_at.desc()).limit(5).all()
            
            if recent_campaigns:
                recent_table = Table(title="Recent Campaigns")
                recent_table.add_column("ID", style="cyan")
                recent_table.add_column("Name", style="white")
                recent_table.add_column("Type", style="yellow")
                recent_table.add_column("Status", style="green")
                recent_table.add_column("Created", style="dim")
                
                for campaign in recent_campaigns:
                    workflows = []
                    if campaign.get_workflow:
                        workflows.append(f"GET:{campaign.get_workflow.name}")
                    if campaign.post_workflow:
                        workflows.append(f"POST:{campaign.post_workflow.name}")
                    workflow_str = ", ".join(workflows) if workflows else "None"
                    recent_table.add_row(
                        str(campaign.id),
                        campaign.name,
                        workflow_str,
                        campaign.status,
                        campaign.created_at.strftime('%Y-%m-%d %H:%M')
                    )
                
                console.print(recent_table)
        
        console.print("[green]✅ Database is accessible[/green]")
        return True
        
    except Exception as e:
        console.print(f"[red]❌ Database error: {e}[/red]")
        return False

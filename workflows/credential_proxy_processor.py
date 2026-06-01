"""
Background processor for credential proxy jobs
"""
from datetime import datetime
from shared.database import db, CredentialProxyJob
from shared.ai_form_detector import AIFormDetector
import logging
import threading
import time
import asyncio

logger = logging.getLogger(__name__)

class CredentialProxyProcessor:
    """Processor for credential proxy jobs"""
    
    def __init__(self):
        self.running = False
        self.thread = None
        self.check_interval = 10  # Check every 10 seconds
    
    def start(self):
        """Start the processor thread"""
        if self.running:
            logger.debug("Credential proxy processor is already running")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._processor_loop, daemon=True)
        self.thread.start()
        logger.info("Credential proxy processor started")
    
    def stop(self):
        """Stop the processor thread"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Credential proxy processor stopped")
    
    def _processor_loop(self):
        """Main processor loop"""
        while self.running:
            try:
                self._process_pending_jobs()
            except Exception as e:
                logger.exception(f"Error in credential proxy processor loop: {e}")
            
            # Sleep for check interval, checking running status periodically
            for _ in range(self.check_interval):
                if not self.running:
                    break
                time.sleep(1)
    
    def _process_pending_jobs(self):
        """Process pending credential proxy jobs"""
        try:
            # Create app context only for querying jobs
            from app import create_admin_app
            app = create_admin_app(skip_plugin_sync=True)
            
            with app.app_context():
                # Find pending jobs and atomically update status to prevent race conditions
                # Use database-level update to ensure only one thread processes each job
                from sqlalchemy import update
                
                # Get pending jobs
                pending_jobs = CredentialProxyJob.query.filter(
                    CredentialProxyJob.status == 'pending'
                ).order_by(CredentialProxyJob.created_at.asc()).limit(5).all()
                
                if not pending_jobs:
                    return
                
                logger.info(f"Found {len(pending_jobs)} pending credential proxy jobs")
                
                # Collect job IDs that were successfully claimed
                claimed_job_ids = []
                
                for job in pending_jobs:
                    try:
                        # Atomically update status to 'processing' to prevent race condition
                        # Only update if still pending (another thread might have grabbed it)
                        updated = db.session.execute(
                            update(CredentialProxyJob)
                            .where(CredentialProxyJob.id == job.id)
                            .where(CredentialProxyJob.status == 'pending')
                            .values(status='processing', started_at=datetime.utcnow())
                        )
                        db.session.commit()
                        
                        # Check if update actually happened (rowcount > 0)
                        if updated.rowcount == 0:
                            logger.debug(f"Job {job.id} was already taken by another thread, skipping")
                            continue
                        
                        claimed_job_ids.append(job.id)
                        
                    except Exception as e:
                        logger.exception(f"Error claiming job {job.id}: {e}")
                
                # Process claimed jobs in background threads (each with its own app context)
                for job_id in claimed_job_ids:
                    def process_job():
                        try:
                            # Create new app context for background thread
                            from app import create_admin_app
                            app = create_admin_app(skip_plugin_sync=True)
                            with app.app_context():
                                # Re-query job in new context
                                job = CredentialProxyJob.query.get(job_id)
                                if job and job.status == 'processing':
                                    self._process_job(job)
                                else:
                                    logger.warning(f"Job {job_id} status changed, skipping")
                        except Exception as e:
                            logger.exception(f"Error processing credential proxy job {job_id}: {e}")
                            try:
                                from app import create_admin_app
                                app = create_admin_app(skip_plugin_sync=True)
                                with app.app_context():
                                    job = CredentialProxyJob.query.get(job_id)
                                    if job:
                                        job.status = 'failed'
                                        job.error_message = str(e)
                                        job.completed_at = datetime.utcnow()
                                        db.session.commit()
                            except Exception:
                                pass
                    
                    thread = threading.Thread(target=process_job, daemon=True)
                    thread.start()
                    
        except Exception as e:
            logger.exception(f"Error processing pending jobs: {e}")
    
    def _process_job(self, job: CredentialProxyJob):
        """Process a single credential proxy job"""
        logger.info(f"Processing credential proxy job {job.id} for campaign {job.campaign_id}")
        
        # Status should already be 'processing' from atomic update, but refresh to ensure
        db.session.refresh(job)
        if job.status != 'processing':
            logger.warning(f"Job {job.id} status is {job.status}, expected 'processing'. Skipping.")
            return
        
        try:
            # Initialize services - create new instance per job to avoid shared state
            from shared.browser_automation import BrowserAutomationService
            automation_service = BrowserAutomationService()
            ai_detector = AIFormDetector(job.ai_config or {})
            
            # Initialize browser automation
            # Check if there's already an event loop in this thread
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
            except RuntimeError:
                # No event loop in this thread, create one
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            try:
                # Initialize browser
                loop.run_until_complete(automation_service.initialize(job.browser_config or {}))
                
                # Process each target site
                results = {}
                target_sites = job.target_sites or []
                
                for site_config in target_sites:
                    site_url = site_config.get('url')
                    if not site_url:
                        logger.warning(f"Skipping site with no URL in job {job.id}")
                        continue
                    timeout_sec = site_config.get('timeout') if site_config.get('timeout') is not None else (job.automation_timeout or 60)
                    timeout_ms = int(timeout_sec) * 1000
                    logger.info(f"Processing target site: {site_url}")
                    page = None
                    
                    try:
                        # Navigate to site
                        page = loop.run_until_complete(
                            automation_service.navigate_to_site(
                                site_url,
                                timeout=timeout_ms
                            )
                        )
                        
                        # Get HTML first (needed for fallback and AI)
                        html = loop.run_until_complete(
                            automation_service.get_page_html(page)
                        )
                        # Take screenshot for AI analysis (optional; e.g. localhost can fail)
                        try:
                            screenshot = loop.run_until_complete(
                                automation_service.take_screenshot(page, full_page=False)
                            )
                        except Exception as screenshot_error:
                            logger.warning(f"Screenshot failed for {site_url}, using HTML-only form detection: {screenshot_error}")
                            screenshot = b''
                        # Detect form fields (AI if screenshot available, else fallback uses HTML)
                        form_fields = ai_detector.detect_form_fields(screenshot, html)
                        logger.info(f"Detected form fields: {form_fields}")
                        
                        # Map credentials to form field selectors
                        # credential_mapping maps credential field names to CSS selectors
                        credential_mapping = {}
                        credentials = job.credentials or {}
                        
                        # Map email/username - try both 'email' and 'username' keys
                        # Normalize to lists for consistency
                        email_field = form_fields.get('email') or form_fields.get('username')
                        if email_field:
                            if isinstance(email_field, str):
                                email_selector = [email_field]
                            elif isinstance(email_field, list):
                                email_selector = email_field
                            else:
                                email_selector = []
                            
                            if email_selector:
                                if 'email' in credentials:
                                    credential_mapping['email'] = email_selector
                                elif 'username' in credentials:
                                    credential_mapping['username'] = email_selector
                        
                        # Map password
                        password_field = form_fields.get('password')
                        if password_field:
                            if isinstance(password_field, str):
                                password_selector = [password_field]
                            elif isinstance(password_field, list):
                                password_selector = password_field
                            else:
                                password_selector = []
                            
                            if password_selector and 'password' in credentials:
                                credential_mapping['password'] = password_selector
                        
                        # Map any other credential fields (if they match detected fields)
                        for cred_field_name in credentials.keys():
                            if cred_field_name not in ['email', 'username', 'password']:
                                # Try to find matching selector in form_fields
                                field_value = form_fields.get(cred_field_name)
                                if field_value:
                                    # Normalize to list
                                    if isinstance(field_value, str):
                                        credential_mapping[cred_field_name] = [field_value]
                                    elif isinstance(field_value, list):
                                        credential_mapping[cred_field_name] = field_value
                        
                        # Validate that we have at least email/username and password mapped
                        has_email_or_username = 'email' in credential_mapping or 'username' in credential_mapping
                        has_password = 'password' in credential_mapping
                        
                        if not has_email_or_username or not has_password:
                            logger.warning(f"Missing required field mappings. Email/Username: {has_email_or_username}, Password: {has_password}")
                            # Fall back to heuristics
                            fallback_fields = ai_detector._fallback_detection(html)
                            if not has_email_or_username:
                                email_selectors = fallback_fields.get('email', [])
                                if email_selectors:
                                    if 'email' in credentials:
                                        credential_mapping['email'] = email_selectors
                                    elif 'username' in credentials:
                                        credential_mapping['username'] = email_selectors
                            if not has_password:
                                password_selectors = fallback_fields.get('password', [])
                                if password_selectors and 'password' in credentials:
                                    credential_mapping['password'] = password_selectors
                        
                        logger.info(f"Credential mapping: {credential_mapping}")
                        
                        # Proxy credentials to site
                        site_result = loop.run_until_complete(
                            automation_service.proxy_credentials_to_site(
                                site_url,
                                credentials,
                                credential_mapping,
                                {
                                    'timeout': timeout_sec,
                                    'submit_button': form_fields.get('submit_button')  # Can be string or list
                                }
                            )
                        )
                        
                        results[site_url] = site_result
                        logger.info(f"Result for {site_url}: {site_result.get('success', False)}")
                        
                    except Exception as e:
                        logger.error(f"Error processing site {site_url}: {e}")
                        results[site_url] = {
                            'success': False,
                            'error': str(e),
                            'url': site_url
                        }
                    finally:
                        # Ensure page is closed even if error occurred
                        if page:
                            try:
                                loop.run_until_complete(page.close())
                            except Exception as e:
                                logger.warning(f"Error closing page for {site_url}: {e}")
                
                # Update job with results
                job.results = results
                job.status = 'completed'
                job.completed_at = datetime.utcnow()
                
                # Count successes
                success_count = sum(1 for r in results.values() if r.get('success', False))
                logger.info(f"Job {job.id} completed: {success_count}/{len(results)} sites successful")
                
            finally:
                # Cleanup browser
                try:
                    loop.run_until_complete(automation_service.cleanup())
                except Exception as e:
                    logger.error(f"Error cleaning up browser: {e}")
                finally:
                    # Close event loop if we created it
                    try:
                        if not loop.is_closed():
                            # Cancel any pending tasks with timeout
                            pending = asyncio.all_tasks(loop)
                            if pending:
                                for task in pending:
                                    task.cancel()
                                # Wait for cancellation with timeout (5 seconds)
                                try:
                                    loop.run_until_complete(
                                        asyncio.wait_for(
                                            asyncio.gather(*pending, return_exceptions=True),
                                            timeout=5.0
                                        )
                                    )
                                except asyncio.TimeoutError:
                                    logger.warning("Event loop task cancellation timed out, forcing close")
                                except Exception as e:
                                    logger.warning(f"Error waiting for task cancellation: {e}")
                            loop.close()
                    except Exception as e:
                        logger.error(f"Error closing event loop: {e}")
            
            db.session.commit()
            
        except Exception as e:
            logger.exception(f"Error processing credential proxy job {job.id}: {e}")
            job.status = 'failed'
            job.error_message = str(e)
            job.completed_at = datetime.utcnow()
            db.session.commit()
            
            # Retry logic
            if job.retry_count < job.max_retries:
                job.retry_count += 1
                job.status = 'pending'
                job.error_message = None
                job.results = {}  # Clear previous results
                job.started_at = None  # Clear started timestamp
                db.session.commit()
                logger.info(f"Retrying job {job.id} (attempt {job.retry_count}/{job.max_retries})")

# Global processor instance
_processor = None

def get_processor() -> CredentialProxyProcessor:
    """Get the global credential proxy processor instance"""
    global _processor
    if _processor is None:
        _processor = CredentialProxyProcessor()
    return _processor

def start_processor():
    """Start the global credential proxy processor"""
    # Only start in main process, not in reloader child process
    # This prevents duplicate starts during Flask debug reloads
    try:
        from werkzeug.serving import is_running_from_reloader
        if is_running_from_reloader():
            logger.debug("Skipping processor start in reloader child process")
            return
    except ImportError:
        # werkzeug.serving not available (e.g., in tests), proceed normally
        pass
    
    get_processor().start()

def stop_processor():
    """Stop the global credential proxy processor"""
    if _processor:
        _processor.stop()


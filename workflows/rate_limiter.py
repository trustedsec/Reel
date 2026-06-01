"""
Rate limiter for email sending workflows using database storage
"""
from typing import Dict, Any
from datetime import datetime, timedelta
from shared.database import db
import logging

logger = logging.getLogger(__name__)

class RateLimiter:
    """Database-backed rate limiter for email sending"""
    
    def __init__(self):
        # In-memory cache for performance, synced with DB
        self._cache = {}
    
    def check_rate_limit(
        self,
        workflow_id: int,
        emails_per_minute: int = 60,
        emails_per_hour: int = 1000
    ) -> Dict[str, Any]:
        """
        Check if sending is allowed based on rate limits
        
        Returns:
            dict with 'allowed' (bool) and 'wait_seconds' (int) if not allowed
        """
        now = datetime.utcnow()
        cache_key = f"workflow_{workflow_id}"
        
        # Check cache first
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            # Refresh cache if stale (older than 1 minute)
            if (now - cached.get('last_check', now)).total_seconds() < 60:
                return cached['result']
        
        # Query database for recent email jobs
        from shared.database import EmailJob, SendingWorkflow
        
        workflow = SendingWorkflow.query.get(workflow_id)
        if not workflow:
            return {'allowed': False, 'wait_seconds': 0, 'error': 'Workflow not found'}
        
        # Count emails sent in last minute
        one_minute_ago = now - timedelta(minutes=1)
        minute_count = EmailJob.query.filter(
            EmailJob.sending_workflow_id == workflow_id,
            EmailJob.status == 'sent',
            EmailJob.sent_at >= one_minute_ago
        ).count()
        
        # Count emails sent in last hour
        one_hour_ago = now - timedelta(hours=1)
        hour_count = EmailJob.query.filter(
            EmailJob.sending_workflow_id == workflow_id,
            EmailJob.status == 'sent',
            EmailJob.sent_at >= one_hour_ago
        ).count()
        
        # Check limits
        result = {'allowed': True, 'wait_seconds': 0}
        
        if minute_count >= emails_per_minute:
            # Calculate wait time until next minute window
            oldest_in_minute = EmailJob.query.filter(
                EmailJob.sending_workflow_id == workflow_id,
                EmailJob.status == 'sent',
                EmailJob.sent_at >= one_minute_ago
            ).order_by(EmailJob.sent_at.asc()).first()
            
            if oldest_in_minute:
                wait_until = oldest_in_minute.sent_at + timedelta(minutes=1)
                wait_seconds = max(0, int((wait_until - now).total_seconds()) + 1)
                result = {'allowed': False, 'wait_seconds': wait_seconds}
        
        if hour_count >= emails_per_hour:
            # Calculate wait time until next hour window
            oldest_in_hour = EmailJob.query.filter(
                EmailJob.sending_workflow_id == workflow_id,
                EmailJob.status == 'sent',
                EmailJob.sent_at >= one_hour_ago
            ).order_by(EmailJob.sent_at.asc()).first()
            
            if oldest_in_hour:
                wait_until = oldest_in_hour.sent_at + timedelta(hours=1)
                wait_seconds = max(0, int((wait_until - now).total_seconds()) + 1)
                # Use the longer wait time
                if wait_seconds > result.get('wait_seconds', 0):
                    result = {'allowed': False, 'wait_seconds': wait_seconds}
        
        # Cache result
        self._cache[cache_key] = {
            'result': result,
            'last_check': now
        }
        
        return result

# Global rate limiter instance
_rate_limiter = None

def get_rate_limiter() -> RateLimiter:
    """Get global rate limiter instance"""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter



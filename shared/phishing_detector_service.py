"""
Service wrapper for phishing detection using plugin system
"""
from typing import Dict, Any, Optional
from plugins import get_plugin
import logging

logger = logging.getLogger(__name__)

class PhishingDetectorService:
    """Service for phishing detection using plugin registry"""
    
    def __init__(self, default_plugin_type: str = 'phishing_detector'):
        """
        Initialize service
        
        Args:
            default_plugin_type: Plugin type to use for detection (default: 'phishing_detector')
        """
        self.default_plugin_type = default_plugin_type
    
    def detect_phishing(
        self,
        html_content: str,
        plugin_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Detect phishing content in HTML
        
        Args:
            html_content: HTML content to analyze
            plugin_type: Optional plugin type override (defaults to self.default_plugin_type)
            
        Returns:
            {
                'is_phishing': bool,
                'confidence': float,  # 0.0-1.0
                'model_name': str,
                'raw_scores': dict,
                'error': Optional[str]
            }
        """
        plugin_type = plugin_type or self.default_plugin_type
        
        try:
            plugin = get_plugin(plugin_type)
            
            if not plugin:
                logger.warning(f"Phishing detector plugin '{plugin_type}' not found")
                return {
                    'is_phishing': False,
                    'confidence': 0.0,
                    'model_name': 'unknown',
                    'raw_scores': {},
                    'error': f'Plugin {plugin_type} not found'
                }
            
            # Execute plugin
            context = {'html_content': html_content}
            config = {'html_content': html_content}
            
            result_context = plugin.execute(context, config)
            
            # Extract detection results
            detection = result_context.get('phishing_detection', {})
            
            if not detection:
                logger.warning("Plugin did not return phishing_detection in context")
                return {
                    'is_phishing': False,
                    'confidence': 0.0,
                    'model_name': plugin_type,
                    'raw_scores': {},
                    'error': 'Plugin did not return detection results'
                }
            
            # Return standardized format
            return {
                'is_phishing': detection.get('is_phishing', False),
                'confidence': detection.get('confidence', 0.0),
                'model_name': detection.get('model_name', plugin_type),
                'raw_scores': detection.get('raw_scores', {}),
                'error': detection.get('error')
            }
            
        except Exception as e:
            logger.error(f"Phishing detection service error: {e}", exc_info=True)
            return {
                'is_phishing': False,
                'confidence': 0.0,
                'model_name': plugin_type or 'unknown',
                'raw_scores': {},
                'error': str(e)
            }

# Global service instance
_service = None

def get_phishing_detector_service() -> PhishingDetectorService:
    """Get global phishing detector service instance"""
    global _service
    if _service is None:
        _service = PhishingDetectorService()
    return _service

def detect_phishing(html_content: str, plugin_type: Optional[str] = None) -> Dict[str, Any]:
    """Convenience function for phishing detection"""
    return get_phishing_detector_service().detect_phishing(html_content, plugin_type)



"""
Phishing detection plugin - uses BERT model to detect phishing content
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
from shared.workflow_variables import interpolate_string
import logging
import re
import html2text

logger = logging.getLogger(__name__)


def _log_phishing_detection_event(context: Dict[str, Any], detection: Dict[str, Any]) -> None:
    """Log a campaign event so the detection score appears in Recent Events."""
    try:
        campaign_id = context.get('campaign_id') or (context.get('campaign') or {}).get('id')
        if not campaign_id:
            return
        from shared.database import db, Event
        error = detection.get('error')
        confidence = detection.get('confidence', 0.0)
        is_phishing = detection.get('is_phishing', False)
        if error:
            summary = f"Skipped: {error}"
        else:
            outcome = "phishing" if is_phishing else "legitimate"
            summary = f"Score {confidence:.2f}, {outcome}"
        event_data = {
            'summary': summary,
            'confidence': confidence,
            'is_phishing': is_phishing,
            'model_name': detection.get('model_name', ''),
        }
        if error:
            event_data['error'] = error
        event = Event(
            campaign_id=campaign_id,
            event_type='phishing_detection',
            data=event_data,
        )
        db.session.add(event)
        db.session.commit()
    except Exception as e:
        logger.debug("Could not log phishing detection event: %s", e)
        try:
            from shared.database import db
            db.session.rollback()
        except Exception:
            pass

DEFAULT_MIN_TEXT_LENGTH = 10


class PhishingDetectorPlugin(BasePlugin):
    """
    Plugin for detecting phishing content using BERT machine learning model.
    
    Analyzes HTML content (email templates, landing pages) using a fine-tuned
    BERT model to detect phishing indicators. Extracts text from HTML, runs
    it through the model, and provides confidence scores for phishing vs legitimate.
    
    Use cases:
    - Analyze email templates for phishing indicators
    - Check landing pages before deployment
    - Quality assurance for campaign content
    - Automated phishing detection in workflows
    - Content analysis and scoring
    
    Requirements:
    - transformers library (pip install transformers torch)
    - BERT model: ealvaradob/bert-finetuned-phishing
    - GPU recommended for faster inference (CPU works but slower)
    
    Example: Analyze campaign template HTML, branch workflow based on
    phishing detection result (phishing/legitimate), and log high-confidence
    phishing detections.
    """
    
    def __init__(self):
        super().__init__()
        self._model = None
        self._tokenizer = None
        self._model_name = "ealvaradob/bert-finetuned-phishing"
        self._model_loaded = False
    
    @property
    def plugin_type(self) -> str:
        return "phishing_detector"
    
    @property
    def display_name(self) -> str:
        return "Phishing Detector (BERT)"
    
    @property
    def description(self) -> str:
        return "Detect phishing content in email templates and landing pages using BERT machine learning model. Analyzes HTML content, extracts text, and provides phishing confidence scores. Use for quality assurance, content analysis, and automated detection. Requires transformers library."
    
    @property
    def plugin_category(self) -> str:
        return "email_validation"
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "html_content": {
                    "type": "string",
                    "title": "HTML Content",
                    "description": "HTML content to analyze for phishing",
                    "help": "HTML content to analyze. Can be email template HTML, landing page HTML, or any HTML content. The plugin extracts text from HTML and analyzes it. Supports variable interpolation: {{template_html}} or {{campaign.template_html}}.",
                    "placeholder": "{{template_html}}, {{campaign.template_html}}, <html>...</html>"
                },
                "max_length": {
                    "type": "integer",
                    "title": "Max Sequence Length",
                    "description": "Maximum token length for BERT model input",
                    "help": "Maximum number of tokens (words/subwords) to analyze. BERT models have token limits (typically 512). Longer content will be truncated. Increase for longer analysis, decrease for faster processing. Range: 1-1024. Default: 512.",
                    "placeholder": "512, 256, 1024",
                    "default": 512,
                    "minimum": 1,
                    "maximum": 1024
                },
                "min_text_length": {
                    "type": "integer",
                    "title": "Minimum Text Length",
                    "description": "Skip BERT inference if extracted text has fewer than this many characters",
                    "help": "Skip BERT inference if extracted text has fewer than this many characters. Set to 0 to always run (results may be unreliable for very short text).",
                    "default": DEFAULT_MIN_TEXT_LENGTH,
                    "minimum": 0,
                    "maximum": 100
                }
            },
            "required": ["html_content"]
        }
    
    def _load_model(self):
        """Lazy load the BERT model and tokenizer"""
        if self._model_loaded:
            return
        
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            import torch
            
            logger.info(f"Loading phishing detection model: {self._model_name}")
            
            self._tokenizer = AutoTokenizer.from_pretrained(self._model_name)
            self._model = AutoModelForSequenceClassification.from_pretrained(self._model_name)
            self._model.eval()  # Set to evaluation mode
            
            # Move to GPU if available
            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self._model.to(device)
            self._device = device
            
            self._model_loaded = True
            logger.info(f"Phishing detection model loaded successfully on {device}")
            
        except ImportError as e:
            logger.error(f"Failed to import transformers/torch: {e}")
            logger.error("Install with: pip install transformers torch")
            raise
        except Exception as e:
            logger.error(f"Failed to load phishing detection model: {e}", exc_info=True)
            raise
    
    def _extract_text_from_html(self, html_content: str) -> str:
        """Extract plain text from HTML content"""
        try:
            # Remove Jinja2 template syntax before analysis
            # Replace {{ }} and {% %} with placeholders
            text = re.sub(r'\{\{.*?\}\}', ' ', html_content)
            text = re.sub(r'\{\%.*?\%\}', ' ', text)
            
            # Convert HTML to text
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = True
            h.body_width = 0  # Don't wrap
            text = h.handle(text)
            
            # Clean up whitespace
            text = re.sub(r'\s+', ' ', text)
            text = text.strip()
            
            return text
            
        except Exception as e:
            logger.warning(f"Failed to extract text from HTML, using fallback: {e}")
            # Fallback: simple regex-based extraction
            text = re.sub(r'<[^>]+>', ' ', html_content)
            text = re.sub(r'\{\{.*?\}\}', ' ', text)
            text = re.sub(r'\{\%.*?\%\}', ' ', text)
            text = re.sub(r'\s+', ' ', text)
            return text.strip()
    
    def _truncate_text(self, text: str, max_length: int = 512) -> str:
        """Truncate text to max token length"""
        if len(text) <= max_length * 4:  # Rough estimate: 4 chars per token
            return text
        
        # Truncate to approximate max tokens
        truncated = text[:max_length * 4]
        # Try to truncate at word boundary
        last_space = truncated.rfind(' ')
        if last_space > max_length * 2:
            truncated = truncated[:last_space]
        
        return truncated
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute phishing detection"""
        try:
            html_content = config.get('html_content') or context.get('html_content', '')
            if isinstance(html_content, str) and html_content.strip():
                html_content = interpolate_string(html_content, context)
            if not html_content:
                logger.warning("No HTML content provided for phishing detection")
                context['phishing_detection'] = {
                    'is_phishing': False,
                    'confidence': 0.0,
                    'model_name': self._model_name,
                    'error': 'No HTML content provided',
                    'raw_scores': {}
                }
                _log_phishing_detection_event(context, context['phishing_detection'])
                return context
            
            # Load model if not already loaded
            try:
                self._load_model()
            except Exception as e:
                logger.error(f"Failed to load model: {e}")
                context['phishing_detection'] = {
                    'is_phishing': False,
                    'confidence': 0.0,
                    'model_name': self._model_name,
                    'error': f'Model loading failed: {str(e)}',
                    'raw_scores': {}
                }
                _log_phishing_detection_event(context, context['phishing_detection'])
                return context
            
            # Extract text from HTML
            text = self._extract_text_from_html(html_content)
            min_text_length = config.get('min_text_length', DEFAULT_MIN_TEXT_LENGTH)
            if not text or len(text.strip()) < min_text_length:
                logger.debug(
                    "Skipping BERT inference (extracted text length %s < min_text_length %s)",
                    len((text or '').strip()), min_text_length
                )
                context['phishing_detection'] = {
                    'is_phishing': False,
                    'confidence': 0.0,
                    'model_name': self._model_name,
                    'error': 'Extracted text too short',
                    'raw_scores': {}
                }
                _log_phishing_detection_event(context, context['phishing_detection'])
                return context
            
            # Truncate if needed
            max_length = config.get('max_length', 512)
            text = self._truncate_text(text, max_length)
            
            # Run inference
            import torch
            
            with torch.no_grad():
                # Tokenize
                inputs = self._tokenizer(
                    text,
                    return_tensors="pt",
                    truncation=True,
                    padding=True,
                    max_length=max_length
                )
                inputs = {k: v.to(self._device) for k, v in inputs.items()}
                
                # Get predictions
                outputs = self._model(**inputs)
                logits = outputs.logits
                
                # Get probabilities
                probabilities = torch.nn.functional.softmax(logits, dim=-1)
                prediction = torch.argmax(probabilities, dim=-1).item()
                
                # Extract scores
                scores = probabilities[0].cpu().tolist()
                confidence = float(max(scores))
                
                # Model outputs: 0 = legitimate, 1 = phishing
                is_phishing = bool(prediction == 1)
            
            result = {
                'is_phishing': is_phishing,
                'confidence': confidence,
                'model_name': self._model_name,
                'raw_scores': {
                    'legitimate': float(scores[0]),
                    'phishing': float(scores[1]) if len(scores) > 1 else 0.0
                },
                'error': None
            }
            
            context['phishing_detection'] = result
            _log_phishing_detection_event(context, result)
            logger.info(f"Phishing detection completed: is_phishing={is_phishing}, confidence={confidence:.3f}")
            
        except Exception as e:
            logger.error(f"Phishing detection failed: {e}", exc_info=True)
            return self.on_error(e, context, config)
        
        return context
    
    def get_branch_context_key(self) -> Optional[str]:
        """Return the context key that contains the phishing detection result"""
        return "phishing_detection.is_phishing"
    
    def get_branch_labels(self) -> Dict[str, str]:
        """Return custom labels for phishing/legitimate paths"""
        return {"true": "Phishing", "false": "Legitimate"}

    def get_branch_colors(self) -> Dict[str, str]:
        """Phishing detected (true) is the negative/red path."""
        return {"true": "red", "false": "green"}

    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate plugin configuration"""
        errors = []
        
        if not config.get('html_content') and not config.get('html_content'):
            errors.append("html_content is required")
        
        max_length = config.get('max_length', 512)
        if not isinstance(max_length, int) or max_length < 1 or max_length > 1024:
            errors.append("max_length must be between 1 and 1024")
        min_text_length = config.get('min_text_length', DEFAULT_MIN_TEXT_LENGTH)
        if not isinstance(min_text_length, int) or min_text_length < 0 or min_text_length > 100:
            errors.append("min_text_length must be between 0 and 100")
        return errors if errors else None



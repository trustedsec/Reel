"""
Tests for the SyncProxyManager and SyncProxySession.
"""
import pytest
import time
import threading
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from workflows.sync_proxy_manager import (
    SyncProxyManager,
    SyncProxySession,
    get_sync_proxy_manager,
    start_sync_proxy_manager,
    stop_sync_proxy_manager,
)


class TestSyncProxyManager:
    def test_create_and_get_session(self):
        manager = SyncProxyManager(session_timeout=60)
        # Don't actually start cleanup thread for unit tests
        manager._running = True

        # Patch SyncProxySession to avoid starting a real browser thread
        with patch.object(SyncProxySession, '__init__', lambda self, *a, **kw: None):
            with patch.object(SyncProxySession, 'session_id', 'mock-sid', create=True):
                session = Mock()
                session.session_id = 'mock-sid'
                session.target_url = 'https://example.com'
                session.created_at = time.time()
                session.last_activity = time.time()
                session.cleanup = Mock()

                manager._sessions['mock-sid'] = session

                result = manager.get_session('mock-sid')
                assert result is session

    def test_remove_session(self):
        manager = SyncProxyManager()
        session = Mock()
        session.session_id = 'test-sid'
        session.cleanup = Mock()
        manager._sessions['test-sid'] = session

        manager.remove_session('test-sid')

        assert 'test-sid' not in manager._sessions
        session.cleanup.assert_called_once()

    def test_remove_nonexistent_session(self):
        manager = SyncProxyManager()
        # Should not raise
        manager.remove_session('nonexistent')

    def test_get_active_count(self):
        manager = SyncProxyManager()
        assert manager.get_active_count() == 0

        manager._sessions['a'] = Mock()
        manager._sessions['b'] = Mock()
        assert manager.get_active_count() == 2

    def test_get_sessions_summary(self):
        manager = SyncProxyManager()
        session = Mock()
        session.session_id = 'sid-1'
        session.target_url = 'https://example.com'
        session.created_at = time.time()
        session.last_activity = time.time()
        manager._sessions['sid-1'] = session

        summary = manager.get_sessions_summary()
        assert len(summary) == 1
        assert summary[0]['session_id'] == 'sid-1'
        assert summary[0]['target_url'] == 'https://example.com'
        assert 'age_seconds' in summary[0]

    def test_expire_idle_sessions(self):
        manager = SyncProxyManager(session_timeout=1)
        session = Mock()
        session.session_id = 'old-sid'
        session.last_activity = time.time() - 10  # 10 seconds ago
        session.cleanup = Mock()
        manager._sessions['old-sid'] = session

        manager._expire_idle_sessions()

        assert 'old-sid' not in manager._sessions
        session.cleanup.assert_called_once()

    def test_non_expired_sessions_kept(self):
        manager = SyncProxyManager(session_timeout=300)
        session = Mock()
        session.session_id = 'fresh-sid'
        session.last_activity = time.time()
        session.cleanup = Mock()
        manager._sessions['fresh-sid'] = session

        manager._expire_idle_sessions()

        assert 'fresh-sid' in manager._sessions
        session.cleanup.assert_not_called()

    def test_stop_cleans_all_sessions(self):
        manager = SyncProxyManager()
        manager._running = True
        s1, s2 = Mock(), Mock()
        s1.cleanup = Mock()
        s2.cleanup = Mock()
        manager._sessions = {'a': s1, 'b': s2}

        manager.stop()

        s1.cleanup.assert_called_once()
        s2.cleanup.assert_called_once()
        assert len(manager._sessions) == 0
        assert manager._running is False

    def test_thread_safety(self):
        """Test that concurrent creates/removes don't corrupt state."""
        manager = SyncProxyManager()

        def create_mock_session(i):
            s = Mock()
            s.session_id = f'sid-{i}'
            s.cleanup = Mock()
            with manager._lock:
                manager._sessions[s.session_id] = s

        def remove_session(i):
            manager.remove_session(f'sid-{i}')

        # Create 10 sessions concurrently
        threads = [threading.Thread(target=create_mock_session, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert manager.get_active_count() == 10

        # Remove 5 concurrently
        threads = [threading.Thread(target=remove_session, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert manager.get_active_count() == 5


class TestSyncProxySessionQueues:
    """Test SyncProxySession queue-based communication without launching a real browser."""

    def test_execute_initial_timeout(self):
        """Test that execute_initial returns timeout error when no browser thread responds."""
        with patch.object(SyncProxySession, '__init__', lambda self, *a, **kw: None):
            import queue as queue_mod
            session = SyncProxySession.__new__(SyncProxySession)
            session._command_queue = queue_mod.Queue()
            session._result_queue = queue_mod.Queue()
            session.last_activity = time.time()

            result = session.execute_initial(timeout=1)
            assert result['status'] == 'failed'
            assert 'timeout' in result['error'].lower()

    def test_execute_continue_timeout(self):
        with patch.object(SyncProxySession, '__init__', lambda self, *a, **kw: None):
            import queue as queue_mod
            session = SyncProxySession.__new__(SyncProxySession)
            session._command_queue = queue_mod.Queue()
            session._result_queue = queue_mod.Queue()
            session.last_activity = time.time()

            result = session.execute_continue('123456', timeout=1)
            assert result['status'] == 'failed'
            assert 'timeout' in result['error'].lower()

    def test_check_status_timeout(self):
        with patch.object(SyncProxySession, '__init__', lambda self, *a, **kw: None):
            import queue as queue_mod
            session = SyncProxySession.__new__(SyncProxySession)
            session._command_queue = queue_mod.Queue()
            session._result_queue = queue_mod.Queue()
            session.last_activity = time.time()

            result = session.check_status(timeout=1)
            assert result['status'] == 'waiting'

    def test_execute_initial_returns_result(self):
        """Simulate the browser thread putting a result in the queue."""
        with patch.object(SyncProxySession, '__init__', lambda self, *a, **kw: None):
            import queue as queue_mod
            session = SyncProxySession.__new__(SyncProxySession)
            session._command_queue = queue_mod.Queue()
            session._result_queue = queue_mod.Queue()
            session.last_activity = time.time()

            # Simulate browser thread responding
            expected = {'status': 'success', 'cookies': []}
            session._result_queue.put(expected)

            result = session.execute_initial(timeout=2)
            assert result == expected

    def test_cleanup(self):
        with patch.object(SyncProxySession, '__init__', lambda self, *a, **kw: None):
            import queue as queue_mod
            session = SyncProxySession.__new__(SyncProxySession)
            session._command_queue = queue_mod.Queue()
            session._result_queue = queue_mod.Queue()
            session._running = True
            session._thread = Mock()
            session._thread.is_alive.return_value = False

            session.cleanup()

            assert session._running is False


class TestCredentialMapping:
    def test_basic_mapping(self):
        form_fields = {
            'email': 'input[name="email"]',
            'password': 'input[type="password"]',
        }
        credentials = {'email': 'user@test.com', 'password': 'pass'}
        ai_detector = Mock()

        result = SyncProxySession._build_credential_mapping(
            form_fields, credentials, ai_detector, '<html></html>'
        )

        assert 'email' in result
        assert result['email'] == ['input[name="email"]']
        assert 'password' in result
        assert result['password'] == ['input[type="password"]']

    def test_username_key_mapping(self):
        form_fields = {'email': 'input#email'}
        credentials = {'username': 'admin', 'password': 'pass'}
        ai_detector = Mock()
        ai_detector._fallback_detection.return_value = {
            'password': ['input[type="password"]']
        }

        result = SyncProxySession._build_credential_mapping(
            form_fields, credentials, ai_detector, '<html></html>'
        )

        assert 'username' in result
        assert result['username'] == ['input#email']

    def test_list_selectors(self):
        form_fields = {
            'email': ['input#email', 'input[name="email"]'],
            'password': ['input#pass'],
        }
        credentials = {'email': 'test@test.com', 'password': 'p'}
        ai_detector = Mock()

        result = SyncProxySession._build_credential_mapping(
            form_fields, credentials, ai_detector, ''
        )

        assert result['email'] == ['input#email', 'input[name="email"]']

    def test_fallback_detection(self):
        form_fields = {}  # AI detection found nothing
        credentials = {'email': 'test@test.com', 'password': 'p'}
        ai_detector = Mock()
        ai_detector._fallback_detection.return_value = {
            'email': ['input[name="email"]'],
            'password': ['input[type="password"]'],
        }

        result = SyncProxySession._build_credential_mapping(
            form_fields, credentials, ai_detector, '<html></html>'
        )

        assert 'email' in result
        assert 'password' in result


class TestModuleFunctions:
    @patch('workflows.sync_proxy_manager._manager', None)
    @patch('workflows.sync_proxy_manager._manager_lock', threading.Lock())
    def test_start_creates_manager(self):
        import workflows.sync_proxy_manager as mod
        mod._manager = None

        with patch.object(SyncProxyManager, 'start'):
            start_sync_proxy_manager(session_timeout=120)
            assert mod._manager is not None
            # Clean up
            mod._manager = None

    @patch('workflows.sync_proxy_manager._manager', None)
    def test_get_returns_none_before_start(self):
        import workflows.sync_proxy_manager as mod
        mod._manager = None
        assert get_sync_proxy_manager() is None

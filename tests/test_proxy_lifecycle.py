from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from upool import app
from upool import api as api_module
from upool import tray as tray_module
from upool.proxy.models import ProxyError


class ClosingEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        return [handler() for handler in self.handlers]


@pytest.fixture
def desktop(monkeypatch):
    """Run the real shell and bridge around an entirely fake webview and tray."""
    service = Mock()
    service.gateway = SimpleNamespace(running=True)
    service.start.side_effect = lambda: setattr(service.gateway, "running", True)
    service.stop.side_effect = lambda: setattr(service.gateway, "running", False)
    service.shutdown.side_effect = lambda: setattr(service.gateway, "running", False)
    monkeypatch.setattr(api_module, "ProxyService", lambda: service)
    monkeypatch.setattr(api_module.Api, "_adopt_live_session", lambda self: None)
    bridge = api_module.Api()
    monkeypatch.setattr(app, "Api", lambda: bridge)
    monkeypatch.setattr(app.autostart, "reconcile", Mock())
    monkeypatch.setenv("UPOOL_DEV_URL", "http://127.0.0.1:3000")

    closing = ClosingEvent()
    destruction_results = []

    def destroy():
        destruction_results.extend(closing.fire())

    window = SimpleNamespace(
        events=SimpleNamespace(closing=closing),
        show=Mock(), hide=Mock(), destroy=Mock(side_effect=destroy),
    )
    shell = SimpleNamespace(create_window=Mock(return_value=window), start=Mock())
    monkeypatch.setitem(sys.modules, "webview", shell)
    trays = []
    tray_available = [True]

    class FakeTray:
        def __init__(self, on_open, on_toggle, on_quit, is_running):
            self.open, self.toggle, self.quit, self.is_running = on_open, on_toggle, on_quit, is_running
            self.available = False
            self.start = Mock(side_effect=self._start)
            self.close = Mock()
            trays.append(self)

        def _start(self):
            self.available = tray_available[0]
            return self.available

    monkeypatch.setattr(app, "Tray", FakeTray)
    return SimpleNamespace(
        service=service, api=bridge, window=window, closing=closing, shell=shell,
        trays=trays, tray_available=tray_available, destruction_results=destruction_results,
    )


def test_window_close_hides_without_stopping_api_when_tray_is_available(desktop):
    def loop(start, **kwargs):
        assert start() is True
        assert desktop.closing.fire() == [False]
        desktop.window.hide.assert_called_once_with()
        desktop.window.destroy.assert_not_called()
        desktop.service.shutdown.assert_not_called()
        desktop.trays[0].close.assert_not_called()
        assert desktop.service.gateway.running is True

    desktop.shell.start.side_effect = loop
    assert app.run([]) == 0
    desktop.service.shutdown.assert_called_once_with()
    desktop.trays[0].close.assert_called_once_with()


def test_quit_destroys_window_and_shutdown_runs_once_across_shell_finally(desktop):
    def loop(start, **kwargs):
        start()
        assert desktop.trays[0].quit() == {"ok": True, "data": True}
        assert desktop.api._exit_requested is True
        assert desktop.destruction_results == [True]
        desktop.window.destroy.assert_called_once_with()
        desktop.window.hide.assert_not_called()
        desktop.service.shutdown.assert_called_once_with()
        desktop.trays[0].close.assert_called_once_with()

    desktop.shell.start.side_effect = loop
    assert app.run([]) == 0
    desktop.window.destroy.assert_called_once_with()
    desktop.service.shutdown.assert_called_once_with()
    desktop.trays[0].close.assert_called_once_with()


def test_failed_tray_start_allows_window_to_close_normally(desktop):
    desktop.tray_available[0] = False

    def loop(start, **kwargs):
        assert start() is False
        assert desktop.closing.fire() == [True]
        desktop.window.hide.assert_not_called()
        desktop.service.shutdown.assert_not_called()

    desktop.shell.start.side_effect = loop
    assert app.run([]) == 0
    desktop.window.destroy.assert_not_called()
    desktop.service.shutdown.assert_called_once_with()


def test_tray_actions_show_window_and_toggle_only_the_managed_gateway(desktop):
    def loop(start, **kwargs):
        start()
        tray = desktop.trays[0]
        tray.open()
        desktop.window.show.assert_called_once_with()
        assert tray.is_running() is True
        tray.toggle()
        desktop.service.stop.assert_called_once_with()
        assert tray.is_running() is False
        tray.toggle()
        desktop.service.start.assert_called_once_with()
        assert tray.is_running() is True

    desktop.shell.start.side_effect = loop
    assert app.run([]) == 0


def test_shell_exception_still_stops_gateway_and_tray(desktop):
    def loop(start, **kwargs):
        start()
        raise RuntimeError("webview stopped unexpectedly")

    desktop.shell.start.side_effect = loop
    with pytest.raises(RuntimeError, match="webview stopped unexpectedly"):
        app.run([])
    desktop.service.shutdown.assert_called_once_with()
    desktop.trays[0].close.assert_called_once_with()


def test_quit_still_destroys_window_and_tray_when_cleanup_reports_an_error(desktop):
    desktop.service.shutdown.side_effect = ProxyError("Managed process cleanup failed.")

    def loop(start, **kwargs):
        start()
        assert desktop.trays[0].quit()["ok"] is False
        assert desktop.destruction_results == [True]
        desktop.window.destroy.assert_called_once_with()
        desktop.trays[0].close.assert_called_once_with()

    desktop.shell.start.side_effect = loop
    assert app.run([]) == 0
    desktop.service.shutdown.assert_called_once_with()


def test_tray_on_unsupported_platform_uses_no_native_calls(monkeypatch):
    monkeypatch.setattr(tray_module, "sys", SimpleNamespace(platform="linux"))
    tray = tray_module.Tray(Mock(), Mock(), Mock(), lambda: False)
    assert tray.start() is False
    assert tray.available is False
    assert tray._thread is None
    tray.close()

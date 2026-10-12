"""Keep the gateway alive when its configuration window is hidden."""

from __future__ import annotations

import ctypes
import sys
import threading
from typing import Callable


class Tray:
    def __init__(self, on_open: Callable, on_toggle: Callable, on_quit: Callable,
                 is_running: Callable[[], bool]) -> None:
        self._open, self._toggle, self._quit = on_open, on_toggle, on_quit
        self._running = is_running
        self._hwnd = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self.available = False

    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        self._thread = threading.Thread(target=self._run, name="upool-tray", daemon=True)
        self._thread.start()
        self._ready.wait(3)
        return self.available

    def close(self) -> None:
        if self._hwnd and sys.platform == "win32":
            user = ctypes.WinDLL("user32", use_last_error=True)
            user.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
            user.PostMessageW(self._hwnd, 0x0010, 0, 0)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def _run(self) -> None:  # pragma: no cover - exercised by Windows package smoke
        from ctypes import wintypes as w
        user = ctypes.WinDLL("user32", use_last_error=True)
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

        class WindowClass(ctypes.Structure):
            _fields_ = [("style", w.UINT), ("callback", callback_type), ("extra_class", ctypes.c_int),
                        ("extra_window", ctypes.c_int), ("instance", w.HINSTANCE), ("icon", w.HICON),
                        ("cursor", w.HANDLE), ("brush", w.HBRUSH), ("menu", w.LPCWSTR), ("name", w.LPCWSTR)]

        class Notification(ctypes.Structure):
            _fields_ = [("size", w.DWORD), ("window", w.HWND), ("id", w.UINT), ("flags", w.UINT),
                        ("message", w.UINT), ("icon", w.HICON), ("tip", w.WCHAR * 128),
                        ("state", w.DWORD), ("state_mask", w.DWORD), ("info", w.WCHAR * 256),
                        ("version", w.UINT), ("title", w.WCHAR * 64), ("info_flags", w.DWORD),
                        ("guid", ctypes.c_byte * 16), ("balloon_icon", w.HICON)]

        user.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        user.DefWindowProcW.restype = ctypes.c_ssize_t
        user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p]
        user.CreateWindowExW.restype = w.HWND
        user.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
        user.LoadIconW.argtypes = [w.HINSTANCE, ctypes.c_void_p]
        user.LoadIconW.restype = w.HICON
        user.CreatePopupMenu.restype = w.HMENU
        user.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]
        user.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, ctypes.c_void_p]
        user.TrackPopupMenu.restype = w.UINT
        user.DestroyMenu.argtypes = [w.HMENU]
        user.SetForegroundWindow.argtypes = [w.HWND]
        user.DestroyWindow.argtypes = [w.HWND]
        user.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
        user.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
        user.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
        user.DispatchMessageW.restype = ctypes.c_ssize_t
        kernel.GetModuleHandleW.argtypes = [w.LPCWSTR]
        kernel.GetModuleHandleW.restype = w.HINSTANCE
        shell.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(Notification)]
        shell.Shell_NotifyIconW.restype = w.BOOL
        taskbar_created = user.RegisterWindowMessageW("TaskbarCreated")
        icon = user.LoadIconW(None, ctypes.c_void_p(32512))
        notification = Notification()

        def action(fn: Callable) -> None:
            def invoke() -> None:
                try:
                    fn()
                except Exception:
                    pass
            threading.Thread(target=invoke, daemon=True).start()

        @callback_type
        def window_proc(hwnd, message, wp, lp):
            if message == taskbar_created:
                shell.Shell_NotifyIconW(0, ctypes.byref(notification))
                return 0
            if message == 0x8001:
                if lp == 0x0203:
                    action(self._open)
                elif lp in (0x0205, 0x007B):
                    menu = user.CreatePopupMenu()
                    user.AppendMenuW(menu, 0, 1, "Open U-Pool")
                    user.AppendMenuW(menu, 0, 2, "Stop API" if self._running() else "Start API")
                    user.AppendMenuW(menu, 0x0800, 0, None)
                    user.AppendMenuW(menu, 0, 3, "Quit")
                    point = w.POINT()
                    user.GetCursorPos(ctypes.byref(point))
                    user.SetForegroundWindow(hwnd)
                    choice = user.TrackPopupMenu(menu, 0x0100 | 0x0002, point.x, point.y, 0, hwnd, None)
                    user.DestroyMenu(menu)
                    if choice in (1, 2, 3):
                        action({1: self._open, 2: self._toggle, 3: self._quit}[choice])
                return 0
            if message == 0x0010:
                shell.Shell_NotifyIconW(2, ctypes.byref(notification))
                user.DestroyWindow(hwnd)
                return 0
            if message == 0x0002:
                self.available = False
                self._hwnd = None
                user.PostQuitMessage(0)
                return 0
            return user.DefWindowProcW(hwnd, message, wp, lp)

        try:
            class_name = "UPoolTray" + str(id(self))
            window_class = WindowClass(0, window_proc, 0, 0, kernel.GetModuleHandleW(None), icon, None, None, None, class_name)
            if not user.RegisterClassW(ctypes.byref(window_class)):
                return
            self._hwnd = user.CreateWindowExW(0, class_name, "U-Pool", 0, 0, 0, 0, 0, None, None, window_class.instance, None)
            if not self._hwnd:
                return
            notification.size = ctypes.sizeof(Notification)
            notification.window, notification.id = self._hwnd, 1
            notification.flags, notification.message, notification.icon = 1 | 2 | 4, 0x8001, icon
            notification.tip = "U-Pool - API gateway"
            self.available = bool(shell.Shell_NotifyIconW(0, ctypes.byref(notification)))
            self._ready.set()
            if not self.available:
                user.DestroyWindow(self._hwnd)
                return
            msg = w.MSG()
            while user.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user.TranslateMessage(ctypes.byref(msg))
                user.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._ready.set()

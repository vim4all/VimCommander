"""The Windows Explorer right-click menu for files and folders (Windows only).

Asks the shell for the items' IContextMenu through ctypes, so the menu is the
same one Explorer shows: Open with, Send to, 7-Zip, Git, Properties and so on.
Submenus that fill themselves in (Send to, Open with) need the window's menu
messages forwarded to the shell; MainWindow.nativeEvent does that through
handle_message() while the menu is open.
"""

import ctypes
import os
from ctypes import wintypes as wt

shell32 = ctypes.windll.shell32
ole32 = ctypes.windll.ole32
user32 = ctypes.windll.user32

HRESULT = ctypes.c_long
LRESULT = ctypes.c_ssize_t


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


ole32.CLSIDFromString.argtypes = [wt.LPCWSTR, ctypes.POINTER(GUID)]
ole32.CoInitialize.argtypes = [ctypes.c_void_p]
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
ole32.CoTaskMemFree.restype = None
shell32.SHParseDisplayName.argtypes = [wt.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                       wt.ULONG, ctypes.POINTER(wt.ULONG)]
shell32.SHParseDisplayName.restype = HRESULT
shell32.SHGetDesktopFolder.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
shell32.SHGetDesktopFolder.restype = HRESULT
shell32.SHBindToParent.argtypes = [ctypes.c_void_p, ctypes.POINTER(GUID),
                                   ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
shell32.SHBindToParent.restype = HRESULT
shell32.ILFindLastID.argtypes = [ctypes.c_void_p]
shell32.ILFindLastID.restype = ctypes.c_void_p
user32.CreatePopupMenu.restype = wt.HMENU
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.TrackPopupMenuEx.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, wt.HWND,
                                    ctypes.c_void_p]
user32.TrackPopupMenuEx.restype = ctypes.c_int
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]


def _guid(text: str) -> GUID:
    g = GUID()
    ole32.CLSIDFromString(text, ctypes.byref(g))
    return g


IID_IShellFolder = _guid("{000214E6-0000-0000-C000-000000000046}")
IID_IContextMenu = _guid("{000214E4-0000-0000-C000-000000000046}")
IID_IContextMenu2 = _guid("{000214F4-0000-0000-C000-000000000046}")
IID_IContextMenu3 = _guid("{BCFCE0B0-EC17-11D0-8D10-00A0C90F2719}")

CMF_EXPLORE, CMF_CANRENAME, CMF_EXTENDEDVERBS = 0x4, 0x10, 0x100
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x2, 0x100
MF_STRING, MF_SEPARATOR = 0x0, 0x800
GCS_VERBW = 4
CMIC_MASK_UNICODE, CMIC_MASK_PTINVOKE = 0x4000, 0x20000000
WM_DRAWITEM, WM_MEASUREITEM, WM_INITMENUPOPUP, WM_MENUCHAR = 0x2B, 0x2C, 0x117, 0x120
FIRST_ID, LAST_SHELL_ID, EXTRA_ID = 1, 0x7FFF, 0x8000


class CMINVOKECOMMANDINFOEX(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("fMask", wt.DWORD), ("hwnd", wt.HWND),
                ("lpVerb", ctypes.c_void_p), ("lpParameters", ctypes.c_char_p),
                ("lpDirectory", ctypes.c_char_p), ("nShow", ctypes.c_int),
                ("dwHotKey", wt.DWORD), ("hIcon", wt.HANDLE), ("lpTitle", ctypes.c_char_p),
                ("lpVerbW", ctypes.c_void_p), ("lpParametersW", wt.LPCWSTR),
                ("lpDirectoryW", wt.LPCWSTR), ("lpTitleW", wt.LPCWSTR), ("ptInvoke", wt.POINT)]


def _call(ptr, index: int, argtypes: list, *args) -> int:
    """Call method `index` of the COM interface at `ptr`."""
    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    method = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *argtypes)(vtable[index])
    return method(ptr, *args)


def _release(ptr: ctypes.c_void_p) -> None:
    if ptr and ptr.value:
        _call(ptr, 2, [])


def _query(ptr: ctypes.c_void_p, iid: GUID) -> ctypes.c_void_p:
    out = ctypes.c_void_p()
    _call(ptr, 0, [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
          ctypes.byref(iid), ctypes.byref(out))
    return out


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} failed (0x{hr & 0xFFFFFFFF:08X})")


def _parse(path: str) -> ctypes.c_void_p:
    pidl = ctypes.c_void_p()
    _check(shell32.SHParseDisplayName(path, None, ctypes.byref(pidl), 0, None), path)
    return pidl


class _Menu:
    """The open menu's IContextMenu2/3, used to answer menu messages."""

    def __init__(self, menu2: ctypes.c_void_p, menu3: ctypes.c_void_p):
        self.menu2, self.menu3 = menu2, menu3

    def handle(self, message: int, wparam: int, lparam: int) -> tuple[bool, int]:
        if message not in (WM_INITMENUPOPUP, WM_DRAWITEM, WM_MEASUREITEM, WM_MENUCHAR):
            return False, 0
        if self.menu3 and self.menu3.value:
            result = LRESULT()
            hr = _call(self.menu3, 7, [wt.UINT, wt.WPARAM, wt.LPARAM, ctypes.POINTER(LRESULT)],
                       message, wparam, lparam, ctypes.byref(result))
            return hr >= 0, result.value
        if self.menu2 and self.menu2.value and message != WM_MENUCHAR:
            hr = _call(self.menu2, 6, [wt.UINT, wt.WPARAM, wt.LPARAM], message, wparam, lparam)
            return hr >= 0, 0
        return False, 0


_active: _Menu | None = None


def handle_message(msg_address: int) -> tuple[bool, int]:
    """Forward a window message to the open shell menu. (handled?, result)"""
    if _active is None:
        return False, 0
    msg = wt.MSG.from_address(msg_address)
    return _active.handle(msg.message, msg.wParam or 0, msg.lParam or 0)


def menu_active() -> bool:
    return _active is not None


def client_to_screen(hwnd: int, x: int, y: int) -> tuple[int, int]:
    """Window client coordinates (physical pixels) to screen coordinates."""
    pt = wt.POINT(x, y)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def show_menu(hwnd: int, paths: list[str], background: bool = False,
              pos: tuple[int, int] | None = None, extended: bool = False,
              extra: list[str] | None = None) -> tuple[str, object] | None:
    """Show Explorer's menu for `paths` (all in one folder), or for the folder
    `paths[0]` itself when `background` is set. `extra` items are added at the end.

    Runs the chosen shell command itself, except for rename and delete, which
    it returns as ("verb", "rename"|"delete") so the app can do them. A chosen
    extra item is returned as ("extra", index). Otherwise returns None.
    Raises OSError if the shell can't provide a menu.
    """
    global _active
    ole32.CoInitialize(None)
    pidls: list[ctypes.c_void_p] = []
    desktop, folder, menu = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    menu2 = menu3 = ctypes.c_void_p()
    hmenu = None
    try:
        if background:
            pidls.append(_parse(paths[0]))
            _check(shell32.SHGetDesktopFolder(ctypes.byref(desktop)), "SHGetDesktopFolder")
            _check(_call(desktop, 5, [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID),
                                      ctypes.POINTER(ctypes.c_void_p)],
                         pidls[0], None, ctypes.byref(IID_IShellFolder), ctypes.byref(folder)),
                   "BindToObject")
            _check(_call(folder, 8, [wt.HWND, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
                         hwnd, ctypes.byref(IID_IContextMenu), ctypes.byref(menu)),
                   "CreateViewObject")
            directory = paths[0]
        else:
            pidls.extend(_parse(p) for p in paths)
            last = ctypes.c_void_p()
            _check(shell32.SHBindToParent(pidls[0], ctypes.byref(IID_IShellFolder),
                                          ctypes.byref(folder), ctypes.byref(last)), "SHBindToParent")
            children = (ctypes.c_void_p * len(pidls))(*[shell32.ILFindLastID(p) for p in pidls])
            _check(_call(folder, 10, [wt.HWND, wt.UINT, ctypes.POINTER(ctypes.c_void_p),
                                      ctypes.POINTER(GUID), ctypes.c_void_p,
                                      ctypes.POINTER(ctypes.c_void_p)],
                         hwnd, len(pidls), children, ctypes.byref(IID_IContextMenu), None,
                         ctypes.byref(menu)), "GetUIObjectOf")
            directory = os.path.dirname(paths[0])

        hmenu = user32.CreatePopupMenu()
        flags = CMF_EXPLORE | CMF_CANRENAME | (CMF_EXTENDEDVERBS if extended else 0)
        _check(_call(menu, 3, [wt.HMENU, wt.UINT, wt.UINT, wt.UINT, wt.UINT],
                     hmenu, 0, FIRST_ID, LAST_SHELL_ID, flags), "QueryContextMenu")
        if extra:
            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            for i, text in enumerate(extra):
                user32.AppendMenuW(hmenu, MF_STRING, EXTRA_ID + i, text)

        menu2, menu3 = _query(menu, IID_IContextMenu2), _query(menu, IID_IContextMenu3)
        if pos is None:
            pt = wt.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            pos = (pt.x, pt.y)
        _active = _Menu(menu2, menu3)
        try:
            cmd = user32.TrackPopupMenuEx(hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON, pos[0], pos[1],
                                          hwnd, None)
        finally:
            _active = None
        if cmd <= 0:
            return None
        if cmd >= EXTRA_ID:
            return "extra", cmd - EXTRA_ID
        verb = _verb(menu, cmd - FIRST_ID)
        if verb in ("rename", "delete"):
            return "verb", verb
        info = CMINVOKECOMMANDINFOEX()
        info.cbSize = ctypes.sizeof(info)
        info.fMask = CMIC_MASK_UNICODE | CMIC_MASK_PTINVOKE
        info.hwnd = hwnd
        info.lpVerb = info.lpVerbW = cmd - FIRST_ID
        info.lpDirectoryW = directory
        info.nShow = 1  # SW_SHOWNORMAL
        info.ptInvoke = wt.POINT(*pos)
        _check(_call(menu, 4, [ctypes.POINTER(CMINVOKECOMMANDINFOEX)], ctypes.byref(info)),
               f"Running '{verb or cmd}'")
        return None
    finally:
        if hmenu:
            user32.DestroyMenu(hmenu)
        for ptr in (menu3, menu2, menu, folder, desktop):
            _release(ptr)
        for pidl in pidls:
            ole32.CoTaskMemFree(pidl)


def _verb(menu: ctypes.c_void_p, offset: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    hr = _call(menu, 5, [ctypes.c_size_t, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT],
               offset, GCS_VERBW, None, ctypes.cast(buf, ctypes.c_void_p), 256)
    return buf.value.lower() if hr >= 0 else ""

"""Wi-Fi em modo de baixa latência enquanto o MultiDesk está ativo (Windows).

O Windows procura outras redes em segundo plano e deixa o adaptador
economizar energia; no Wi-Fi isso gera picos de 100-300 ms no ping mesmo com
a rede boa. Como fazem o Moonlight e o Steam Link, pedimos o "modo de
streaming" e desligamos a varredura em segundo plano. Os dois valem só
enquanto o handle está aberto: ao fechar (ou o processo terminar) o Windows
volta ao normal sozinho.
"""

import logging
import sys

log = logging.getLogger("multidesk.wifi")


class _Noop:
    def close(self):
        pass


def low_latency():
    """Liga o modo de baixa latência; retorna um objeto com close()."""
    if sys.platform != "win32":
        return _Noop()
    try:
        return _WlanLowLatency()
    except Exception as e:  # sem Wi-Fi, serviço WLAN parado etc.
        log.debug("modo de baixa latência do Wi-Fi indisponível: %s", e)
        return _Noop()


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _BACKGROUND_SCAN_ENABLED = 2  # WLAN_INTF_OPCODE
    _MEDIA_STREAMING_MODE = 3
    _STATE_CONNECTED = 1

    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    class _IfaceInfo(ctypes.Structure):
        _fields_ = [("guid", _GUID), ("description", ctypes.c_wchar * 256), ("state", ctypes.c_int)]

    class _IfaceList(ctypes.Structure):
        _fields_ = [("count", wintypes.DWORD), ("index", wintypes.DWORD), ("items", _IfaceInfo * 1)]

    class _WlanLowLatency:
        def __init__(self):
            api = self._api = ctypes.WinDLL("wlanapi.dll")
            api.WlanOpenHandle.argtypes = (wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
                                           ctypes.POINTER(wintypes.HANDLE))
            api.WlanEnumInterfaces.argtypes = (wintypes.HANDLE, ctypes.c_void_p,
                                               ctypes.POINTER(ctypes.POINTER(_IfaceList)))
            api.WlanSetInterface.argtypes = (wintypes.HANDLE, ctypes.POINTER(_GUID), ctypes.c_int,
                                             wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p)
            api.WlanFreeMemory.argtypes = (ctypes.c_void_p,)
            api.WlanCloseHandle.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
            for fn in ("WlanOpenHandle", "WlanEnumInterfaces", "WlanSetInterface", "WlanCloseHandle"):
                getattr(api, fn).restype = wintypes.DWORD

            self._handle = wintypes.HANDLE()
            version = wintypes.DWORD()
            err = api.WlanOpenHandle(2, None, ctypes.byref(version), ctypes.byref(self._handle))
            if err:
                raise OSError(err, "WlanOpenHandle")
            lst = ctypes.POINTER(_IfaceList)()
            err = api.WlanEnumInterfaces(self._handle, None, ctypes.byref(lst))
            if err:
                self.close()
                raise OSError(err, "WlanEnumInterfaces")
            try:
                n = lst.contents.count
                items = ctypes.cast(ctypes.byref(lst.contents.items), ctypes.POINTER(_IfaceInfo * n)).contents
                done = []
                for it in items:
                    if it.state != _STATE_CONNECTED:
                        continue
                    off, on = wintypes.BOOL(False), wintypes.BOOL(True)
                    a = api.WlanSetInterface(self._handle, ctypes.byref(it.guid), _BACKGROUND_SCAN_ENABLED,
                                             ctypes.sizeof(off), ctypes.byref(off), None)
                    b = api.WlanSetInterface(self._handle, ctypes.byref(it.guid), _MEDIA_STREAMING_MODE,
                                             ctypes.sizeof(on), ctypes.byref(on), None)
                    if a == 0 or b == 0:
                        done.append(it.description)
                    if a or b:
                        log.debug("Wi-Fi %s: erro varredura=%s streaming=%s", it.description, a, b)
            finally:
                api.WlanFreeMemory(lst)
            if done:
                log.info("Wi-Fi em modo de baixa latência: %s", ", ".join(done))

        def close(self):
            if self._handle:
                self._api.WlanCloseHandle(self._handle, None)
                self._handle = None

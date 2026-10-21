"""Reads account lockout (4740) and unlock (4767) events from the PDC emulator's Security log.

Every lockout in a domain is forwarded to the PDC emulator, so its Security log is
the one place that records where lockouts came from. Events are read through the
Windows Event Log API (wevtapi.dll) as the account signed in to this tool. The
credentials are handed to the API in-process and never appear on a command line.
Reading requires that account to be in Event Log Readers on the DCs.
"""

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime

import defusedxml.ElementTree as ElementTree

EVENT_LOCKED_OUT = 4740
EVENT_UNLOCKED = 4767
DEFAULT_LIMIT = 50
EVENT_LOG_TIMEOUT_MS = 60_000

_NS = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}

# Win32 error codes the Event Log API reports.
_ERROR_ACCESS_DENIED = 5
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_NO_MORE_ITEMS = 259
_ERROR_LOGON_FAILURE = 1326
_ERROR_TIMEOUT = 1460
_ERROR_RPC_UNAVAILABLE = 1722
_ERROR_ACCOUNT_LOCKED_OUT = 1909

ACCESS_DENIED_HELP = (
    "Access to the Security log on {pdc} was denied for {identity}.\n\n"
    "To allow this, a domain admin adds {identity} to the built-in 'Event Log Readers' group "
    "(Builtin container in Active Directory Users and Computers) and enables the "
    "'Remote Event Log Management' firewall rules on the domain controllers."
)
RPC_UNAVAILABLE_HELP = (
    "Could not reach the event log service on {pdc}.\n\n"
    "Enable the 'Remote Event Log Management' firewall rules on the domain controllers."
)


class LockoutEventsError(RuntimeError):
    pass


@dataclass
class EventLogCredentials:
    domain: str
    user: str
    password: str = field(repr=False)  # never in logs, tracebacks, or reprs

    @property
    def display(self):
        return f"{self.domain}\\{self.user}" if self.domain else self.user


@dataclass
class LockoutEvent:
    time: datetime
    event_id: int
    dc: str
    detail: str  # caller computer for a lockout, unlocking account for an unlock

    @property
    def kind(self):
        return "Locked out" if self.event_id == EVENT_LOCKED_OUT else "Unlocked"


def build_query(sam_account_name):
    """XPath for lockout and unlock events of one account.

    XPath 1.0 has no escape sequences, so the quote style is chosen to avoid
    any quote in the name. sAMAccountName can legally contain an apostrophe.
    """
    if "'" not in sam_account_name:
        literal = f"'{sam_account_name}'"
    elif '"' not in sam_account_name:
        literal = f'"{sam_account_name}"'
    else:
        raise LockoutEventsError("This account name contains both quote characters and cannot be searched.")
    return (
        f"*[System[(EventID={EVENT_LOCKED_OUT} or EventID={EVENT_UNLOCKED})]"
        f" and EventData[Data[@Name='TargetUserName']={literal}]]"
    )


# --- Windows Event Log API (wevtapi.dll) -------------------------------------------------

_EVT_RPC_LOGIN_CLASS = 1  # EvtRpcLogin
_EVT_RPC_LOGIN_AUTH_NEGOTIATE = 1
_EVT_QUERY_CHANNEL_PATH = 0x1
_EVT_QUERY_FORWARD_DIRECTION = 0x100
_EVT_QUERY_REVERSE_DIRECTION = 0x200
_EVT_RENDER_EVENT_XML = 1


class _EvtRpcLogin(ctypes.Structure):
    _fields_ = [
        ("Server", wintypes.LPWSTR),
        ("User", wintypes.LPWSTR),
        ("Domain", wintypes.LPWSTR),
        ("Password", wintypes.LPWSTR),
        ("Flags", wintypes.DWORD),
    ]


_wevtapi = None


def _api():
    """Load wevtapi.dll and declare signatures once. Only available on Windows."""
    global _wevtapi
    if _wevtapi is None:
        if sys.platform != "win32":
            raise LockoutEventsError("Reading event logs requires Windows.")
        api = ctypes.WinDLL("wevtapi", use_last_error=True)
        handle = wintypes.HANDLE
        api.EvtOpenSession.argtypes = [wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
        api.EvtOpenSession.restype = handle
        api.EvtQuery.argtypes = [handle, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        api.EvtQuery.restype = handle
        api.EvtNext.argtypes = [
            handle,
            wintypes.DWORD,
            ctypes.POINTER(handle),
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        api.EvtNext.restype = wintypes.BOOL
        api.EvtRender.argtypes = [
            handle,
            handle,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        api.EvtRender.restype = wintypes.BOOL
        api.EvtClose.argtypes = [handle]
        api.EvtClose.restype = wintypes.BOOL
        _wevtapi = api
    return _wevtapi


def _raise_last_error():
    raise ctypes.WinError(ctypes.get_last_error())


def _render_xml(api, event):
    used = wintypes.DWORD(0)
    count = wintypes.DWORD(0)
    if not api.EvtRender(None, event, _EVT_RENDER_EVENT_XML, 0, None, ctypes.byref(used), ctypes.byref(count)):
        if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
            _raise_last_error()
    buffer = ctypes.create_unicode_buffer(used.value // ctypes.sizeof(ctypes.c_wchar) + 1)
    if not api.EvtRender(
        None, event, _EVT_RENDER_EVENT_XML, ctypes.sizeof(buffer), buffer, ctypes.byref(used), ctypes.byref(count)
    ):
        _raise_last_error()
    return buffer.value


def _read_event_xml(server, credentials, query, limit, newest_first):
    """Event XML strings from the remote Security log. Raises OSError (with winerror) on failure.

    credentials of None means the caller's own Windows identity (used only for diagnostics).
    """
    api = _api()
    login = _EvtRpcLogin(
        Server=server,
        User=credentials.user if credentials else None,
        Domain=(credentials.domain or None) if credentials else None,
        Password=credentials.password if credentials else None,
        Flags=_EVT_RPC_LOGIN_AUTH_NEGOTIATE,
    )
    session = result_set = None
    events = []
    try:
        session = api.EvtOpenSession(_EVT_RPC_LOGIN_CLASS, ctypes.byref(login), 0, 0)
        if not session:
            _raise_last_error()
        direction = _EVT_QUERY_REVERSE_DIRECTION if newest_first else _EVT_QUERY_FORWARD_DIRECTION
        result_set = api.EvtQuery(session, "Security", query, _EVT_QUERY_CHANNEL_PATH | direction)
        if not result_set:
            _raise_last_error()

        batch = (wintypes.HANDLE * min(limit, 50))()
        while len(events) < limit:
            returned = wintypes.DWORD(0)
            wanted = min(len(batch), limit - len(events))
            if not api.EvtNext(result_set, wanted, batch, EVENT_LOG_TIMEOUT_MS, 0, ctypes.byref(returned)):
                if ctypes.get_last_error() == _ERROR_NO_MORE_ITEMS:
                    break
                _raise_last_error()
            for index in range(returned.value):
                try:
                    events.append(_render_xml(api, batch[index]))
                finally:
                    api.EvtClose(batch[index])
        return events
    finally:
        if result_set:
            api.EvtClose(result_set)
        if session:
            api.EvtClose(session)
        # Drop the reference to the password string held by the login structure.
        login.Password = None


def _read(pdc, credentials, query, limit, newest_first):
    """_read_event_xml with Win32 errors turned into messages an operator can act on."""
    identity = credentials.display if credentials else "the current Windows account"
    try:
        return _read_event_xml(pdc, credentials, query, limit, newest_first)
    except OSError as exc:
        code = getattr(exc, "winerror", None)
        if code == _ERROR_ACCESS_DENIED:
            raise LockoutEventsError(ACCESS_DENIED_HELP.format(pdc=pdc, identity=identity)) from exc
        if code == _ERROR_RPC_UNAVAILABLE:
            raise LockoutEventsError(RPC_UNAVAILABLE_HELP.format(pdc=pdc)) from exc
        if code == _ERROR_LOGON_FAILURE:
            raise LockoutEventsError(f"{pdc} rejected the credentials for {identity}.") from exc
        if code == _ERROR_ACCOUNT_LOCKED_OUT:
            raise LockoutEventsError(f"{identity} is locked out, so {pdc} refused the connection.") from exc
        if code == _ERROR_TIMEOUT:
            raise LockoutEventsError(f"Reading the Security log on {pdc} timed out.") from exc
        raise LockoutEventsError(f"Reading the Security log on {pdc} failed: {exc}") from exc


# --- Parsing ---------------------------------------------------------------------------------


def _parse_time(text):
    # e.g. 2026-10-19T14:06:12.1234567Z. Trim fractional seconds Python can't take.
    if not text:
        return None
    head = text.rstrip("Z").split(".")[0]
    try:
        return datetime.fromisoformat(head + "+00:00").astimezone()
    except ValueError:
        return None


def parse_events(xml_text):
    """Turn concatenated <Event> elements into LockoutEvent objects, newest first."""
    if not xml_text.strip():
        return []
    root = ElementTree.fromstring(f"<Events>{xml_text}</Events>")
    events = []
    for event in root.findall("e:Event", _NS):
        system = event.find("e:System", _NS)
        event_id = int(system.findtext("e:EventID", default="0", namespaces=_NS))
        created = system.find("e:TimeCreated", _NS)
        data = {item.get("Name"): (item.text or "") for item in event.findall("e:EventData/e:Data", _NS)}
        if event_id == EVENT_LOCKED_OUT:
            # 4740 stores the caller computer in TargetDomainName.
            detail = data.get("TargetDomainName", "")
        else:
            subject = data.get("SubjectUserName", "")
            domain = data.get("SubjectDomainName", "")
            detail = f"{domain}\\{subject}" if domain else subject
        events.append(
            LockoutEvent(
                time=_parse_time(created.get("SystemTime") if created is not None else ""),
                event_id=event_id,
                dc=system.findtext("e:Computer", default="", namespaces=_NS),
                detail=detail,
            )
        )
    events.sort(key=lambda item: item.time or datetime.min.astimezone(), reverse=True)
    return events


def query_events(pdc, sam_account_name, credentials, limit=DEFAULT_LIMIT):
    xml_events = _read(pdc, credentials, build_query(sam_account_name), limit, newest_first=True)
    return parse_events("".join(xml_events))


def oldest_event_time(pdc, credentials):
    """Time of the oldest event still in the PDC's Security log, i.e. how far back history goes."""
    xml_events = _read(pdc, credentials, "*", 1, newest_first=False)
    if not xml_events:
        return None
    root = ElementTree.fromstring(f"<Events>{xml_events[0]}</Events>")
    created = root.find("e:Event/e:System/e:TimeCreated", _NS)
    return _parse_time(created.get("SystemTime")) if created is not None else None


@dataclass
class LockoutEventsResult:
    pdc: str
    identity: str
    events: list
    oldest: datetime = None


def load_lockout_events(pdc, sam_account_name, credentials):
    events = query_events(pdc, sam_account_name, credentials)
    try:
        oldest = oldest_event_time(pdc, credentials)
    except LockoutEventsError:
        oldest = None
    return LockoutEventsResult(pdc=pdc, identity=credentials.display, events=events, oldest=oldest)

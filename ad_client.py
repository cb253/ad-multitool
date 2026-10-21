"""LDAP wrapper around ldap3 for common AD admin operations."""

import logging
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ldap3 import BASE, MODIFY_REPLACE, SIMPLE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPCommunicationError
from ldap3.protocol.formatters.formatters import format_sid
from ldap3.utils.conv import escape_filter_chars

from lockout_events import EventLogCredentials

_logger = logging.getLogger(__name__)

_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
_ACCOUNT_EXPIRES_NEVER = 0x7FFFFFFFFFFFFFFF

LDAPS_PORT = 636
CONNECT_TIMEOUT_SECONDS = 10
RECEIVE_TIMEOUT_SECONDS = 30
PAGE_SIZE = 500

UAC_COMPUTED_ATTRIBUTE = "msDS-User-Account-Control-Computed"

USER_ATTRIBUTES = [
    "sAMAccountName",
    "cn",
    "distinguishedName",
    "objectSid",
    "userAccountControl",
    UAC_COMPUTED_ATTRIBUTE,
    "adminCount",
    "lockoutTime",
    "lastLogonTimestamp",
    "memberOf",
    "mail",
    "userPrincipalName",
    "title",
    "department",
    "description",
    "physicalDeliveryOfficeName",
    "telephoneNumber",
    "manager",
    "whenCreated",
    "whenChanged",
    "pwdLastSet",
    "accountExpires",
]

COMPUTER_ATTRIBUTES = [
    "sAMAccountName",
    "cn",
    "distinguishedName",
    "dNSHostName",
    "operatingSystem",
    "operatingSystemVersion",
    "description",
    "userAccountControl",
    "whenCreated",
    "whenChanged",
    "lastLogonTimestamp",
]

# userAccountControl bit for a disabled account
UAC_ACCOUNTDISABLE = 0x2
# userAccountControl bit set on domain controller computer accounts
UAC_SERVER_TRUST_ACCOUNT = 0x2000
# msDS-User-Account-Control-Computed bit for an account that is locked out right now.
# Unlike lockoutTime, AD clears this once the lockout duration has passed.
UF_LOCKOUT = 0x10

# Well-known RIDs and SIDs for accounts and groups the tool refuses to disable.
PRIVILEGED_ACCOUNT_RIDS = {500: "built-in Administrator account", 502: "krbtgt service account"}
PRIVILEGED_GROUP_RIDS = {512: "Domain Admins", 518: "Schema Admins", 519: "Enterprise Admins"}
PRIVILEGED_GROUP_SIDS = {"S-1-5-32-544": "Administrators"}

# AD-specific LDAP matching rule that walks nested group membership
# transitively, instead of only the immediate member/memberOf attribute.
LDAP_MATCHING_RULE_IN_CHAIN = "1.2.840.113556.1.4.1941"
# Matching rule for "attribute has all of these bits set".
LDAP_MATCHING_RULE_BIT_AND = "1.2.840.113556.1.4.803"

# LDAP result code for a truncated result set (server-side size limit hit).
LDAP_SIZE_LIMIT_EXCEEDED = 4

# OID of the paged results control, used to read the paging cookie.
PAGED_RESULTS_CONTROL = "1.2.840.113556.1.4.319"


def filetime_to_datetime(value):
    if not value or int(value) == 0:
        return None
    return _FILETIME_EPOCH + timedelta(microseconds=int(value) / 10)


def account_expires_to_datetime(value):
    if not value:
        return None
    ivalue = int(value)
    if ivalue == 0 or ivalue >= _ACCOUNT_EXPIRES_NEVER:
        return None
    return filetime_to_datetime(ivalue)


def generalized_time_to_datetime(value):
    """Parse AD's whenCreated/whenChanged format, e.g. '20240101120000.0Z'."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).rstrip("Z").split(".")[0]
    try:
        return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def build_filter_value(query):
    """Turn user search input into an LDAP filter value.

    If the query already contains a '*' wildcard, honor it as typed
    (e.g. 'lap*' matches names starting with 'lap'). Otherwise treat
    the whole query as a plain substring and wrap it in wildcards.
    """
    query = query.strip()
    if "*" in query:
        return "*".join(escape_filter_chars(part) for part in query.split("*"))
    return f"*{escape_filter_chars(query)}*"


def cn_from_dn(dn):
    """Extract the leading CN value from a distinguished name."""
    if not dn:
        return ""
    first_rdn = dn.split(",")[0]
    return first_rdn.split("=", 1)[1] if "=" in first_rdn else first_rdn


def container_from_dn(dn):
    """Return the DN with the leading RDN (e.g. CN=user) stripped off."""
    parts = dn.split(",")
    return ",".join(parts[1:]) if len(parts) > 1 else ""


def dns_domain_from_base_dn(base_dn):
    """'DC=example,DC=com' -> 'example.com'. Ignores non-DC components."""
    labels = []
    for part in base_dn.split(","):
        key, _, value = part.strip().partition("=")
        if key.strip().upper() == "DC" and value:
            labels.append(value.strip())
    return ".".join(labels)


def qualify_server_name(server, base_dn):
    """Expand a short DC name ('dc01') to its FQDN using the base DN's domain.

    The DC's LDAPS certificate is issued to its FQDN, so a short name fails
    certificate hostname validation. IPs and names that already have a dot are left alone.
    """
    server = server.strip()
    domain = dns_domain_from_base_dn(base_dn)
    if not server or "." in server or ":" in server or not domain:
        return server
    return f"{server}.{domain}"


def sid_to_string(value):
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return format_sid(value)


def rid_from_sid(sid):
    try:
        return int(sid.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None


def _value(entry, attribute):
    return entry[attribute].value if attribute in entry else None


@dataclass
class ADUser:
    sam_account_name: str
    common_name: str
    distinguished_name: str
    email: str = ""
    member_of: list = field(default_factory=list)
    object_sid: str = ""
    locked_out: bool = False
    locked_out_at: datetime = None
    admin_count: int = 0
    last_logon: datetime = None
    disabled: bool = False
    user_principal_name: str = ""
    title: str = ""
    department: str = ""
    description: str = ""
    office: str = ""
    phone: str = ""
    manager_dn: str = ""
    when_created: datetime = None
    when_changed: datetime = None
    password_last_set: datetime = None
    account_expires: datetime = None

    @property
    def is_locked_out(self):
        return self.locked_out

    @property
    def manager_name(self):
        return cn_from_dn(self.manager_dn)

    @property
    def organizational_unit(self):
        return container_from_dn(self.distinguished_name)

    @classmethod
    def from_entry(cls, entry):
        uac = int(_value(entry, "userAccountControl") or 0)
        uac_computed = int(_value(entry, UAC_COMPUTED_ATTRIBUTE) or 0)
        locked_out = bool(uac_computed & UF_LOCKOUT)
        return cls(
            sam_account_name=str(_value(entry, "sAMAccountName") or ""),
            common_name=str(_value(entry, "cn") or ""),
            distinguished_name=str(_value(entry, "distinguishedName") or entry.entry_dn),
            email=str(_value(entry, "mail") or ""),
            member_of=[str(dn) for dn in entry["memberOf"].values] if "memberOf" in entry else [],
            object_sid=sid_to_string(_value(entry, "objectSid")),
            locked_out=locked_out,
            locked_out_at=filetime_to_datetime(_value(entry, "lockoutTime")) if locked_out else None,
            admin_count=int(_value(entry, "adminCount") or 0),
            last_logon=filetime_to_datetime(_value(entry, "lastLogonTimestamp")),
            disabled=bool(uac & UAC_ACCOUNTDISABLE),
            user_principal_name=str(_value(entry, "userPrincipalName") or ""),
            title=str(_value(entry, "title") or ""),
            department=str(_value(entry, "department") or ""),
            description=str(_value(entry, "description") or ""),
            office=str(_value(entry, "physicalDeliveryOfficeName") or ""),
            phone=str(_value(entry, "telephoneNumber") or ""),
            manager_dn=str(_value(entry, "manager") or ""),
            when_created=generalized_time_to_datetime(_value(entry, "whenCreated")),
            when_changed=generalized_time_to_datetime(_value(entry, "whenChanged")),
            password_last_set=filetime_to_datetime(_value(entry, "pwdLastSet")),
            account_expires=account_expires_to_datetime(_value(entry, "accountExpires")),
        )


@dataclass
class ADComputer:
    sam_account_name: str
    common_name: str
    distinguished_name: str
    dns_hostname: str = ""
    operating_system: str = ""
    operating_system_version: str = ""
    description: str = ""
    disabled: bool = False
    when_created: datetime = None
    when_changed: datetime = None
    last_logon: datetime = None

    @property
    def organizational_unit(self):
        return container_from_dn(self.distinguished_name)

    @classmethod
    def from_entry(cls, entry):
        uac = int(_value(entry, "userAccountControl") or 0)
        return cls(
            sam_account_name=str(_value(entry, "sAMAccountName") or ""),
            common_name=str(_value(entry, "cn") or ""),
            distinguished_name=str(_value(entry, "distinguishedName") or entry.entry_dn),
            dns_hostname=str(_value(entry, "dNSHostName") or ""),
            operating_system=str(_value(entry, "operatingSystem") or ""),
            operating_system_version=str(_value(entry, "operatingSystemVersion") or ""),
            description=str(_value(entry, "description") or ""),
            disabled=bool(uac & UAC_ACCOUNTDISABLE),
            when_created=generalized_time_to_datetime(_value(entry, "whenCreated")),
            when_changed=generalized_time_to_datetime(_value(entry, "whenChanged")),
            last_logon=filetime_to_datetime(_value(entry, "lastLogonTimestamp")),
        )


def _make_server(host):
    """LDAPS server definition with certificate and hostname validation against the Windows trust store."""
    return Server(
        host,
        port=LDAPS_PORT,
        use_ssl=True,
        tls=Tls(validate=ssl.CERT_REQUIRED, sni=host),
        get_info=None,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
    )


def ad_interval_to_timedelta(value):
    """AD stores policy durations as negative counts of 100 ns intervals."""
    if value in (None, ""):
        return None
    if isinstance(value, timedelta):
        return abs(value)
    ivalue = int(value)
    if ivalue in (0, -0x8000000000000000):  # 0 or "never" (until an admin unlocks)
        return None
    return timedelta(microseconds=abs(ivalue) / 10)


@dataclass
class LockoutPolicy:
    threshold: int
    duration: timedelta  # None means locked until an admin unlocks
    observation_window: timedelta


@dataclass
class DcLockoutStatus:
    dc: str
    bad_password_count: int = None
    last_bad_password: datetime = None
    last_logon: datetime = None
    lockout_time: datetime = None
    error: str = ""


DC_LOCKOUT_ATTRIBUTES = ["badPwdCount", "badPasswordTime", "lastLogon", "lockoutTime"]


def _read_dc_lockout_status(dc, user, password, user_dn):
    """One DC's view of an account, over its own validated LDAPS connection."""
    conn = Connection(
        _make_server(dc),
        user=user,
        password=password,
        authentication=SIMPLE,
        receive_timeout=RECEIVE_TIMEOUT_SECONDS,
    )
    try:
        conn.bind()
        if not conn.bound:
            return DcLockoutStatus(dc, error=conn.result.get("description") or "bind failed")
        conn.search(user_dn, "(objectClass=user)", search_scope=BASE, attributes=DC_LOCKOUT_ATTRIBUTES)
        if not conn.entries:
            return DcLockoutStatus(dc, error="account not found on this DC")
        entry = conn.entries[0]
        count = _value(entry, "badPwdCount")
        return DcLockoutStatus(
            dc,
            bad_password_count=int(count) if count not in (None, "") else None,
            last_bad_password=filetime_to_datetime(_value(entry, "badPasswordTime")),
            last_logon=filetime_to_datetime(_value(entry, "lastLogon")),
            lockout_time=filetime_to_datetime(_value(entry, "lockoutTime")),
        )
    except Exception as exc:
        _logger.warning("Lockout status read from %s failed", dc, exc_info=True)
        return DcLockoutStatus(dc, error=str(exc).splitlines()[0][:200])
    finally:
        try:
            conn.unbind()
        except Exception:
            _logger.debug("Ignoring error closing connection to %s", dc, exc_info=True)


class SessionLockedError(RuntimeError):
    """Raised when an operation is attempted while signed out (e.g. during a session lock)."""


@dataclass
class SearchResult:
    items: list
    truncated: bool = False


class ADClient:
    """Thread-safe wrapper around one bound ldap3 connection.

    ldap3's default SYNC strategy keeps the last operation's result on the
    connection object, so every operation and the read of its result happen
    under one lock. Background workers and the UI thread can share a client.
    """

    def __init__(self, server, base_dn):
        self.base_dn = base_dn
        self.server_name = server
        self._server = _make_server(server)
        self._conn = None
        self._lock = threading.RLock()
        self.domain = None
        self.username = None
        self.bound_identity = None

    def connect(self, domain, username, password):
        # Simple bind (UPN-style user@domain), not NTLM: NTLM needs MD4,
        # which OpenSSL 3.x disables by default. The password is only ever
        # sent inside the certificate-validated LDAPS tunnel.
        if "@" in username or "=" in username:
            user = username
        elif domain:
            user = f"{username}@{domain}"
        else:
            user = username

        with self._lock:
            self._conn = Connection(
                self._server,
                user=user,
                password=password,
                authentication=SIMPLE,
                receive_timeout=RECEIVE_TIMEOUT_SECONDS,
                auto_bind=True,
            )
            self.domain = domain
            self.username = username
            # "u:DOMAIN\\user" as the DC sees it, the identity actually used for writes.
            identity = self._conn.extend.standard.who_am_i() or ""
            self.bound_identity = identity[2:] if identity.startswith("u:") else identity or username

    def reauthenticate(self, password):
        """Bind again as the same user after a session lock."""
        expected_identity = self.bound_identity
        self.connect(self.domain, self.username, password)
        if expected_identity and self.bound_identity.lower() != expected_identity.lower():
            # Should not happen with an unchanged username, but never resume someone else's session.
            self.close()
            raise RuntimeError(f"Signed in as {self.bound_identity}, expected {expected_identity}.")

    @property
    def is_connected(self):
        return self._conn is not None

    @property
    def bound_sam_account_name(self):
        identity = self.bound_identity or self.username or ""
        return identity.rsplit("\\", 1)[-1].split("@", 1)[0]

    def close(self):
        """Unbind and drop the connection, which also discards the password ldap3 keeps for rebinding."""
        with self._lock:
            if self._conn:
                self._conn.unbind()
                self._conn = None

    def _reconnect(self):
        _logger.warning("LDAP connection to %s dropped, rebinding", self.server_name)
        try:
            self._conn.unbind()
        except Exception:
            _logger.debug("Ignoring error while closing the dropped connection", exc_info=True)
        self._conn.bind()

    def _run(self, operation):
        """Run one ldap3 call under the lock, rebinding once if the DC dropped the connection."""
        with self._lock:
            if self._conn is None:
                raise SessionLockedError("Not signed in. Unlock the session and try again.")
            try:
                return operation()
            except LDAPCommunicationError:
                self._reconnect()
                return operation()

    def _search(self, search_base, search_filter, attributes, scope=SUBTREE):
        """Paged search. Returns (entries, truncated)."""

        def operation():
            entries = []
            truncated = False
            cookie = None
            while True:
                self._conn.search(
                    search_base,
                    search_filter,
                    search_scope=scope,
                    attributes=attributes,
                    paged_size=PAGE_SIZE,
                    paged_cookie=cookie,
                )
                entries.extend(self._conn.entries)
                if self._conn.result.get("result") == LDAP_SIZE_LIMIT_EXCEEDED:
                    truncated = True
                controls = self._conn.result.get("controls") or {}
                cookie = controls.get(PAGED_RESULTS_CONTROL, {}).get("value", {}).get("cookie")
                if not cookie:
                    return entries, truncated

        return self._run(operation)

    def _modify(self, distinguished_name, changes):
        def operation():
            self._conn.modify(distinguished_name, changes)
            if self._conn.result["description"] != "success":
                raise RuntimeError(self._conn.result.get("message") or self._conn.result["description"])

        self._run(operation)

    def search_users(self, query):
        """Search by partial sAMAccountName, cn, or mail. Supports '*' wildcards."""
        term = build_filter_value(query)
        filter_str = f"(&(objectClass=user)(objectCategory=person)(|(sAMAccountName={term})(cn={term})(mail={term})))"
        entries, truncated = self._search(self.base_dn, filter_str, USER_ATTRIBUTES)
        return SearchResult([ADUser.from_entry(entry) for entry in entries], truncated)

    def get_user(self, sam_account_name):
        """Exact-match lookup by sAMAccountName, used for user-to-user comparison."""
        term = escape_filter_chars(sam_account_name.strip())
        filter_str = f"(&(objectClass=user)(objectCategory=person)(sAMAccountName={term}))"
        entries, _ = self._search(self.base_dn, filter_str, USER_ATTRIBUTES)
        return ADUser.from_entry(entries[0]) if entries else None

    def search_computers(self, query):
        """Search by partial sAMAccountName, cn, or DNS hostname. Supports '*' wildcards."""
        term = build_filter_value(query)
        filter_str = f"(&(objectClass=computer)(|(sAMAccountName={term})(cn={term})(dNSHostName={term})))"
        entries, truncated = self._search(self.base_dn, filter_str, COMPUTER_ATTRIBUTES)
        return SearchResult([ADComputer.from_entry(entry) for entry in entries], truncated)

    def get_group_members(self, group_dn):
        """Every user in this group, including via nested group membership."""
        term = escape_filter_chars(group_dn)
        filter_str = f"(&(objectClass=user)(objectCategory=person)(memberOf:{LDAP_MATCHING_RULE_IN_CHAIN}:={term}))"
        entries, truncated = self._search(self.base_dn, filter_str, USER_ATTRIBUTES)
        return SearchResult([ADUser.from_entry(entry) for entry in entries], truncated)

    def get_group_description(self, group_dn):
        """Description of a single group by DN, or "" if it has none or doesn't exist."""
        entries, _ = self._search(group_dn, "(objectClass=group)", ["description"], scope=BASE)
        return str(_value(entries[0], "description") or "") if entries else ""

    def _effective_group_entries(self, member_dn, attributes):
        term = escape_filter_chars(member_dn)
        filter_str = f"(&(objectClass=group)(member:{LDAP_MATCHING_RULE_IN_CHAIN}:={term}))"
        entries, _ = self._search(self.base_dn, filter_str, attributes)
        return entries

    def get_effective_groups_with_descriptions(self, member_dn):
        """Every group this user/computer belongs to, including nested groups, as (name, description) pairs."""
        entries = self._effective_group_entries(member_dn, ["cn", "description"])
        groups = [(str(_value(entry, "cn")), str(_value(entry, "description") or "")) for entry in entries]
        return sorted(groups, key=lambda group: group[0].lower())

    def get_privileged_groups(self, member_dn):
        """Names of the privileged groups (Domain Admins etc.) this account is in, directly or nested."""
        names = []
        for entry in self._effective_group_entries(member_dn, ["cn", "objectSid"]):
            sid = sid_to_string(_value(entry, "objectSid"))
            label = PRIVILEGED_GROUP_SIDS.get(sid) or PRIVILEGED_GROUP_RIDS.get(rid_from_sid(sid))
            if label:
                names.append(str(_value(entry, "cn") or label))
        return sorted(names, key=str.lower)

    def disable_block_reason(self, user):
        """Why this account must not be disabled from the tool, or None if it is safe to disable."""
        if user.sam_account_name.lower() == self.bound_sam_account_name.lower():
            return "this is the account you are signed in with"
        rid = rid_from_sid(user.object_sid)
        if rid in PRIVILEGED_ACCOUNT_RIDS:
            return PRIVILEGED_ACCOUNT_RIDS[rid]
        privileged_groups = self.get_privileged_groups(user.distinguished_name)
        if privileged_groups:
            return "member of " + ", ".join(privileged_groups)
        if user.admin_count:
            return "protected admin account (adminCount=1)"
        return None

    def get_lockout_policy(self):
        attributes = ["lockoutThreshold", "lockoutDuration", "lockOutObservationWindow"]
        entries, _ = self._search(self.base_dn, "(objectClass=domain)", attributes, scope=BASE)
        if not entries:
            return None
        entry = entries[0]
        return LockoutPolicy(
            threshold=int(_value(entry, "lockoutThreshold") or 0),
            duration=ad_interval_to_timedelta(_value(entry, "lockoutDuration")),
            observation_window=ad_interval_to_timedelta(_value(entry, "lockOutObservationWindow")),
        )

    def get_domain_controllers(self):
        """DNS names of every domain controller (SERVER_TRUST_ACCOUNT computers)."""
        filter_str = (
            f"(&(objectCategory=computer)(userAccountControl:{LDAP_MATCHING_RULE_BIT_AND}:={UAC_SERVER_TRUST_ACCOUNT}))"
        )
        entries, _ = self._search(self.base_dn, filter_str, ["dNSHostName"])
        return sorted(
            (str(_value(entry, "dNSHostName")) for entry in entries if _value(entry, "dNSHostName")), key=str.lower
        )

    def get_pdc_hostname(self):
        """DNS name of the PDC emulator, where every lockout in the domain is recorded."""
        entries, _ = self._search(self.base_dn, "(objectClass=domain)", ["fSMORoleOwner"], scope=BASE)
        ntds_settings_dn = str(_value(entries[0], "fSMORoleOwner") or "") if entries else ""
        if not ntds_settings_dn:
            return ""
        server_dn = container_from_dn(ntds_settings_dn)
        entries, _ = self._search(server_dn, "(objectClass=server)", ["dNSHostName"], scope=BASE)
        return str(_value(entries[0], "dNSHostName") or "") if entries else ""

    def event_log_credentials(self):
        """The signed-in account's credentials, for reading DC event logs as that same account."""
        with self._lock:
            if self._conn is None:
                raise SessionLockedError("Not signed in. Unlock the session and try again.")
            identity = self.bound_identity or ""
            if "\\" in identity:
                domain, user = identity.split("\\", 1)
            else:
                # No DOMAIN\user from the DC. The UPN bind name works on its own with no domain.
                domain, user = "", self._conn.user
            return EventLogCredentials(domain=domain, user=user, password=self._conn.password)

    def get_lockout_status_per_dc(self, user_dn, domain_controllers=None):
        """Read the non-replicated lockout attributes for one account from every DC, in parallel.

        badPwdCount, badPasswordTime, and lastLogon are tracked separately on each DC, so the DC
        with the most recent bad password usually points at where the bad attempts came from.
        """
        with self._lock:
            if self._conn is None:
                raise SessionLockedError("Not signed in. Unlock the session and try again.")
            user, password = self._conn.user, self._conn.password
        if domain_controllers is None:
            domain_controllers = self.get_domain_controllers()
        if not domain_controllers:
            return []
        with ThreadPoolExecutor(max_workers=min(8, len(domain_controllers))) as pool:
            return list(pool.map(lambda dc: _read_dc_lockout_status(dc, user, password, user_dn), domain_controllers))

    def unlock_account(self, distinguished_name):
        self._modify(distinguished_name, {"lockoutTime": [(MODIFY_REPLACE, [0])]})

    def disable_account(self, distinguished_name):
        """Set the ACCOUNTDISABLE bit, keeping every other userAccountControl flag as-is.

        The current value is re-read right before the write, under the same lock,
        so a flag another admin changed since the search is not reverted.
        """
        with self._lock:
            entries, _ = self._search(distinguished_name, "(objectClass=user)", ["userAccountControl"], scope=BASE)
            if not entries:
                raise RuntimeError("account no longer exists")
            current = int(_value(entries[0], "userAccountControl") or 0)
            self._modify(
                distinguished_name,
                {"userAccountControl": [(MODIFY_REPLACE, [current | UAC_ACCOUNTDISABLE])]},
            )

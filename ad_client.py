"""LDAP wrapper around ldap3 for common AD admin operations."""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from ldap3 import Server, Connection, SIMPLE, SUBTREE, MODIFY_REPLACE
from ldap3.utils.conv import escape_filter_chars

_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
_ACCOUNT_EXPIRES_NEVER = 0x7FFFFFFFFFFFFFFF

USER_ATTRIBUTES = [
    "sAMAccountName",
    "cn",
    "distinguishedName",
    "userAccountControl",
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

# AD-specific LDAP matching rule that walks nested group membership
# transitively, instead of only the immediate member/memberOf attribute.
LDAP_MATCHING_RULE_IN_CHAIN = "1.2.840.113556.1.4.1941"

# LDAP result code for a truncated result set (server-side size limit hit).
LDAP_SIZE_LIMIT_EXCEEDED = 4


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


@dataclass
class ADUser:
    sam_account_name: str
    common_name: str
    distinguished_name: str
    email: str = ""
    member_of: list = field(default_factory=list)
    locked_out_at: datetime = None
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
        return self.locked_out_at is not None

    @property
    def manager_name(self):
        return cn_from_dn(self.manager_dn)

    @property
    def organizational_unit(self):
        return container_from_dn(self.distinguished_name)

    @classmethod
    def from_entry(cls, entry):
        uac = int(entry.userAccountControl.value or 0)
        return cls(
            sam_account_name=str(entry.sAMAccountName.value or ""),
            common_name=str(entry.cn.value or ""),
            distinguished_name=str(entry.distinguishedName.value),
            email=str(entry.mail.value or ""),
            member_of=[str(dn) for dn in entry.memberOf.values] if entry.memberOf else [],
            locked_out_at=filetime_to_datetime(entry.lockoutTime.value),
            last_logon=filetime_to_datetime(entry.lastLogonTimestamp.value),
            disabled=bool(uac & UAC_ACCOUNTDISABLE),
            user_principal_name=str(entry.userPrincipalName.value or ""),
            title=str(entry.title.value or ""),
            department=str(entry.department.value or ""),
            description=str(entry.description.value or ""),
            office=str(entry.physicalDeliveryOfficeName.value or ""),
            phone=str(entry.telephoneNumber.value or ""),
            manager_dn=str(entry.manager.value or ""),
            when_created=generalized_time_to_datetime(entry.whenCreated.value),
            when_changed=generalized_time_to_datetime(entry.whenChanged.value),
            password_last_set=filetime_to_datetime(entry.pwdLastSet.value),
            account_expires=account_expires_to_datetime(entry.accountExpires.value),
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
        uac = int(entry.userAccountControl.value or 0)
        return cls(
            sam_account_name=str(entry.sAMAccountName.value or ""),
            common_name=str(entry.cn.value or ""),
            distinguished_name=str(entry.distinguishedName.value),
            dns_hostname=str(entry.dNSHostName.value or ""),
            operating_system=str(entry.operatingSystem.value or ""),
            operating_system_version=str(entry.operatingSystemVersion.value or ""),
            description=str(entry.description.value or ""),
            disabled=bool(uac & UAC_ACCOUNTDISABLE),
            when_created=generalized_time_to_datetime(entry.whenCreated.value),
            when_changed=generalized_time_to_datetime(entry.whenChanged.value),
            last_logon=filetime_to_datetime(entry.lastLogonTimestamp.value),
        )


class ADClient:
    def __init__(self, server, base_dn, use_ssl=True):
        self.base_dn = base_dn
        self._server = Server(server, use_ssl=use_ssl, get_info=None)
        self._conn = None
        self.username = None
        self.last_search_truncated = False

    def connect(self, domain, username, password):
        # Simple bind (UPN-style user@domain), not NTLM: NTLM needs MD4,
        # which OpenSSL 3.x disables by default.
        if "@" in username or "=" in username:
            user = username
        elif domain:
            user = f"{username}@{domain}"
        else:
            user = username

        self._conn = Connection(
            self._server,
            user=user,
            password=password,
            authentication=SIMPLE,
            auto_bind=True,
        )
        self.username = username

    def close(self):
        if self._conn:
            self._conn.unbind()
            self._conn = None

    def search_users(self, query):
        """Search by partial sAMAccountName, cn, or mail. Supports '*' wildcards."""
        term = build_filter_value(query)
        filter_str = (
            f"(&(objectClass=user)(objectCategory=person)"
            f"(|(sAMAccountName={term})(cn={term})(mail={term})))"
        )
        self._conn.search(
            self.base_dn,
            filter_str,
            search_scope=SUBTREE,
            attributes=USER_ATTRIBUTES,
        )
        self.last_search_truncated = self._conn.result.get("result") == LDAP_SIZE_LIMIT_EXCEEDED
        return [ADUser.from_entry(entry) for entry in self._conn.entries]

    def get_user(self, sam_account_name):
        """Exact-match lookup by sAMAccountName, used for user-to-user comparison."""
        term = escape_filter_chars(sam_account_name.strip())
        filter_str = f"(&(objectClass=user)(objectCategory=person)(sAMAccountName={term}))"
        self._conn.search(
            self.base_dn,
            filter_str,
            search_scope=SUBTREE,
            attributes=USER_ATTRIBUTES,
        )
        if not self._conn.entries:
            return None
        return ADUser.from_entry(self._conn.entries[0])

    def search_computers(self, query):
        """Search by partial sAMAccountName, cn, or DNS hostname. Supports '*' wildcards."""
        term = build_filter_value(query)
        filter_str = (
            f"(&(objectClass=computer)"
            f"(|(sAMAccountName={term})(cn={term})(dNSHostName={term})))"
        )
        self._conn.search(
            self.base_dn,
            filter_str,
            search_scope=SUBTREE,
            attributes=COMPUTER_ATTRIBUTES,
        )
        self.last_search_truncated = self._conn.result.get("result") == LDAP_SIZE_LIMIT_EXCEEDED
        return [ADComputer.from_entry(entry) for entry in self._conn.entries]

    def get_group_members(self, group_dn):
        """Every user in this group, including via nested group membership."""
        term = escape_filter_chars(group_dn)
        filter_str = (
            f"(&(objectClass=user)(objectCategory=person)"
            f"(memberOf:{LDAP_MATCHING_RULE_IN_CHAIN}:={term}))"
        )
        self._conn.search(
            self.base_dn,
            filter_str,
            search_scope=SUBTREE,
            attributes=USER_ATTRIBUTES,
        )
        return [ADUser.from_entry(entry) for entry in self._conn.entries]

    def get_effective_groups(self, member_dn):
        """Every group this user/computer belongs to, including nested groups."""
        term = escape_filter_chars(member_dn)
        filter_str = f"(&(objectClass=group)(member:{LDAP_MATCHING_RULE_IN_CHAIN}:={term}))"
        self._conn.search(
            self.base_dn,
            filter_str,
            search_scope=SUBTREE,
            attributes=["cn"],
        )
        return sorted((str(entry.cn.value) for entry in self._conn.entries), key=str.lower)

    def unlock_account(self, distinguished_name):
        self._conn.modify(
            distinguished_name,
            {"lockoutTime": [(MODIFY_REPLACE, [0])]},
        )
        if self._conn.result["description"] != "success":
            raise RuntimeError(self._conn.result.get("message") or self._conn.result["description"])

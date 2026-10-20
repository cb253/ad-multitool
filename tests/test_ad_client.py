import pytest
from ldap3 import MOCK_SYNC, Connection, Server

from ad_client import UAC_ACCOUNTDISABLE, UF_LOCKOUT, ADClient, SessionLockedError

BASE_DN = "DC=test,DC=local"
OPERATOR_DN = f"CN=Operator,{BASE_DN}"
JDOE_DN = f"CN=Jane Doe,OU=Users,{BASE_DN}"

UAC_NORMAL_ACCOUNT = 0x200
UAC_DONT_EXPIRE_PASSWORD = 0x10000


def _user(sam, cn, **extra):
    attributes = {
        "objectClass": ["top", "person", "organizationalPerson", "user"],
        "objectCategory": "person",
        "sAMAccountName": sam,
        "cn": cn,
        "userAccountControl": UAC_NORMAL_ACCOUNT,
    }
    attributes.update(extra)
    return attributes


@pytest.fixture
def client():
    conn = Connection(Server("mock"), user=OPERATOR_DN, password="pw", client_strategy=MOCK_SYNC)
    conn.strategy.add_entry(OPERATOR_DN, _user("operator", "Operator", userPassword="pw"))
    conn.strategy.add_entry(
        JDOE_DN,
        _user(
            "jdoe",
            "Jane Doe",
            distinguishedName=JDOE_DN,
            mail="jdoe@test.local",
            objectSid="S-1-5-21-1-2-3-1105",
        ),
    )
    conn.bind()

    ad = ADClient("dc01.test.local", BASE_DN)
    ad._conn = conn
    ad.username = "operator"
    ad.bound_identity = "TEST\\operator"
    return ad


def _set(client, dn, attribute, value):
    client._conn.strategy.entries[dn][attribute] = [str(value).encode()]


def test_search_users_returns_matches(client):
    result = client.search_users("jdoe")
    assert [user.sam_account_name for user in result.items] == ["jdoe"]
    assert result.truncated is False


def test_stale_lockout_time_is_not_reported_as_locked(client):
    # AD leaves lockoutTime set after the lockout duration passes. Only the computed bit is current.
    _set(client, JDOE_DN, "lockoutTime", 133000000000000000)
    _set(client, JDOE_DN, "msDS-User-Account-Control-Computed", 0)
    user = client.get_user("jdoe")
    assert user.is_locked_out is False
    assert user.locked_out_at is None


def test_current_lockout_is_reported(client):
    _set(client, JDOE_DN, "lockoutTime", 133000000000000000)
    _set(client, JDOE_DN, "msDS-User-Account-Control-Computed", UF_LOCKOUT)
    user = client.get_user("jdoe")
    assert user.is_locked_out is True
    assert user.locked_out_at is not None


def test_unlock_clears_lockout_time(client):
    _set(client, JDOE_DN, "lockoutTime", 133000000000000000)
    client.unlock_account(JDOE_DN)
    assert client._conn.strategy.entries[JDOE_DN]["lockoutTime"] == [b"0"]


def test_disable_keeps_other_flags_changed_after_search(client):
    user = client.get_user("jdoe")
    # Another admin sets "password never expires" after our search returned.
    _set(client, JDOE_DN, "userAccountControl", UAC_NORMAL_ACCOUNT | UAC_DONT_EXPIRE_PASSWORD)

    client.disable_account(user.distinguished_name)

    uac = int(client._conn.strategy.entries[JDOE_DN]["userAccountControl"][0])
    assert uac == UAC_NORMAL_ACCOUNT | UAC_DONT_EXPIRE_PASSWORD | UAC_ACCOUNTDISABLE
    assert client.get_user("jdoe").disabled is True


def test_disable_missing_account_raises(client):
    with pytest.raises(RuntimeError):
        client.disable_account(f"CN=Nobody,{BASE_DN}")


def test_group_description_for_missing_group_is_empty(client):
    assert client.get_group_description(f"CN=Missing,{BASE_DN}") == ""


class TestDisableBlockReason:
    @pytest.fixture(autouse=True)
    def no_privileged_groups(self, client, monkeypatch):
        # The mock directory can't evaluate AD's transitive membership matching rule.
        monkeypatch.setattr(client, "get_privileged_groups", lambda dn: [])

    def test_normal_user_is_allowed(self, client):
        assert client.disable_block_reason(client.get_user("jdoe")) is None

    def test_own_account_is_blocked(self, client):
        reason = client.disable_block_reason(client.get_user("operator"))
        assert "signed in" in reason

    def test_builtin_administrator_is_blocked(self, client):
        _set(client, JDOE_DN, "objectSid", "S-1-5-21-1-2-3-500")
        assert "Administrator" in client.disable_block_reason(client.get_user("jdoe"))

    def test_admin_count_is_blocked(self, client):
        _set(client, JDOE_DN, "adminCount", 1)
        assert "adminCount" in client.disable_block_reason(client.get_user("jdoe"))

    def test_privileged_group_member_is_blocked(self, client, monkeypatch):
        monkeypatch.setattr(client, "get_privileged_groups", lambda dn: ["Domain Admins"])
        assert "Domain Admins" in client.disable_block_reason(client.get_user("jdoe"))


class _FakeAttribute:
    def __init__(self, value):
        self.value = value


class _FakeEntry(dict):
    def __getitem__(self, key):
        return _FakeAttribute(dict.__getitem__(self, key))


def test_privileged_groups_matched_by_sid_not_name(client, monkeypatch):
    entries = [
        _FakeEntry(cn="Renamed DA", objectSid="S-1-5-21-1-2-3-512"),
        _FakeEntry(cn="Administrators", objectSid="S-1-5-32-544"),
        _FakeEntry(cn="Domain Admins Fan Club", objectSid="S-1-5-21-1-2-3-4001"),
    ]
    monkeypatch.setattr(client, "_effective_group_entries", lambda dn, attributes: entries)
    assert client.get_privileged_groups(JDOE_DN) == ["Administrators", "Renamed DA"]


def test_operations_refused_after_close(client):
    client.close()
    assert client.is_connected is False
    with pytest.raises(SessionLockedError):
        client.search_users("jdoe")
    with pytest.raises(SessionLockedError):
        client.unlock_account(JDOE_DN)


def test_reauthenticate_rejects_a_different_identity(client, monkeypatch):
    def connect_as_someone_else(domain, username, password):
        client._conn = object()
        client.bound_identity = r"TEST\someoneelse"

    monkeypatch.setattr(client, "connect", connect_as_someone_else)
    monkeypatch.setattr(client, "close", lambda: setattr(client, "_conn", None))
    with pytest.raises(RuntimeError, match="expected TEST"):
        client.reauthenticate("pw")
    assert client.is_connected is False

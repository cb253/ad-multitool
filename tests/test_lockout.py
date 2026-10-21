from datetime import datetime, timedelta, timezone

import pytest

import ad_client
import lockout_events
from ad_client import DcLockoutStatus, LockoutPolicy, ad_interval_to_timedelta
from lockout_events import EventLogCredentials, LockoutEventsError, build_query, parse_events
from tabs.lockout_dialog import describe_policy, sort_dc_statuses

EVENTS_XML = """\
<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>
  <System><EventID>4740</EventID><TimeCreated SystemTime='2026-10-19T14:06:12.1234567Z'/>
    <Computer>DC01.example.com</Computer></System>
  <EventData><Data Name='TargetUserName'>jdoe</Data><Data Name='TargetDomainName'>LAP-JDOE</Data>
    <Data Name='SubjectUserName'>DC01$</Data><Data Name='SubjectDomainName'>EXAMPLE</Data></EventData>
</Event>
<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>
  <System><EventID>4767</EventID><TimeCreated SystemTime='2026-10-19T15:00:00Z'/>
    <Computer>DC02.example.com</Computer></System>
  <EventData><Data Name='TargetUserName'>jdoe</Data><Data Name='TargetDomainName'>EXAMPLE</Data>
    <Data Name='SubjectUserName'>operator</Data><Data Name='SubjectDomainName'>EXAMPLE</Data></EventData>
</Event>
"""


class TestBuildQuery:
    def test_plain_name(self):
        query = build_query("jdoe")
        assert "EventID=4740 or EventID=4767" in query
        assert "Data[@Name='TargetUserName']='jdoe'" in query

    def test_name_with_apostrophe_uses_double_quotes(self):
        assert '="o\'brien"' in build_query("o'brien")

    def test_name_with_both_quotes_is_refused(self):
        with pytest.raises(LockoutEventsError):
            build_query("a'b\"c")


class TestParseEvents:
    def test_lockout_and_unlock_fields(self):
        unlock, lockout = parse_events(EVENTS_XML)  # newest first

        assert lockout.kind == "Locked out"
        assert lockout.dc == "DC01.example.com"
        assert lockout.detail == "LAP-JDOE"  # caller computer lives in TargetDomainName for 4740
        assert lockout.time == datetime(2026, 10, 19, 14, 6, 12, tzinfo=timezone.utc)

        assert unlock.kind == "Unlocked"
        assert unlock.dc == "DC02.example.com"
        assert unlock.detail == "EXAMPLE\\operator"

    def test_empty_output(self):
        assert parse_events("") == []

    def test_entity_expansion_is_rejected(self):
        hostile = "<!DOCTYPE x [<!ENTITY a 'aaaa'>]><Event>&a;</Event>"
        with pytest.raises(Exception):  # noqa: B017 (defusedxml raises its own types)
            parse_events(hostile)


CREDENTIALS = EventLogCredentials(domain="EXAMPLE", user="admin-operator", password="s3cret")


class TestEventLogErrors:
    def _failing_read(self, winerror, seen=None):
        def read(server, credentials, query, limit, newest_first):
            if seen is not None:
                seen.append(credentials)
            raise OSError(None, "simulated", None, winerror)

        return read

    def test_reads_as_the_tool_account_not_windows(self, monkeypatch):
        seen = []
        monkeypatch.setattr(lockout_events, "_read_event_xml", self._failing_read(5, seen))
        with pytest.raises(LockoutEventsError, match=r"denied for EXAMPLE\\admin-operator"):
            lockout_events.query_events("DC01.example.com", "jdoe", CREDENTIALS)
        assert seen == [CREDENTIALS]

    def test_access_denied_explains_the_fix(self, monkeypatch):
        monkeypatch.setattr(lockout_events, "_read_event_xml", self._failing_read(5))
        with pytest.raises(LockoutEventsError, match="Event Log Readers"):
            lockout_events.query_events("DC01.example.com", "jdoe", CREDENTIALS)

    def test_rpc_unavailable_mentions_firewall(self, monkeypatch):
        monkeypatch.setattr(lockout_events, "_read_event_xml", self._failing_read(1722))
        with pytest.raises(LockoutEventsError, match="firewall"):
            lockout_events.query_events("DC01.example.com", "jdoe", CREDENTIALS)

    def test_bad_credentials(self, monkeypatch):
        monkeypatch.setattr(lockout_events, "_read_event_xml", self._failing_read(1326))
        with pytest.raises(LockoutEventsError, match="rejected the credentials"):
            lockout_events.query_events("DC01.example.com", "jdoe", CREDENTIALS)

    def test_timeout(self, monkeypatch):
        monkeypatch.setattr(lockout_events, "_read_event_xml", self._failing_read(1460))
        with pytest.raises(LockoutEventsError, match="timed out"):
            lockout_events.query_events("DC01.example.com", "jdoe", CREDENTIALS)

    def test_events_are_parsed_and_identity_reported(self, monkeypatch):
        chunks = EVENTS_XML.split("</Event>")
        xml_events = [chunk + "</Event>" for chunk in chunks if chunk.strip()]
        monkeypatch.setattr(lockout_events, "_read_event_xml", lambda *args: xml_events)
        result = lockout_events.load_lockout_events("DC01.example.com", "jdoe", CREDENTIALS)
        assert result.identity == "EXAMPLE\\admin-operator"
        assert [event.kind for event in result.events] == ["Unlocked", "Locked out"]


def test_credentials_repr_hides_password():
    assert "s3cret" not in repr(CREDENTIALS)
    assert "s3cret" not in str(CREDENTIALS)


class TestEventLogCredentialsFromClient:
    def _client(self, bound_identity, bind_user="admin-operator@example.com"):
        client = ad_client.ADClient("dc01.test.local", "DC=test,DC=local")

        class FakeConn:
            user, password = bind_user, "pw"

        client._conn = FakeConn()
        client.bound_identity = bound_identity
        return client

    def test_uses_bound_identity(self):
        credentials = self._client("EXAMPLE\\admin-operator").event_log_credentials()
        assert (credentials.domain, credentials.user, credentials.password) == ("EXAMPLE", "admin-operator", "pw")

    def test_falls_back_to_upn(self):
        credentials = self._client("").event_log_credentials()
        assert (credentials.domain, credentials.user) == ("", "admin-operator@example.com")

    def test_refused_when_signed_out(self):
        client = ad_client.ADClient("dc01.test.local", "DC=test,DC=local")
        with pytest.raises(ad_client.SessionLockedError):
            client.event_log_credentials()


class TestPolicy:
    def test_interval_conversion(self):
        assert ad_interval_to_timedelta("-18000000000") == timedelta(minutes=30)
        assert ad_interval_to_timedelta(-0x8000000000000000) is None
        assert ad_interval_to_timedelta(0) is None

    def test_describe_policy(self):
        policy = LockoutPolicy(5, timedelta(minutes=30), timedelta(minutes=30))
        assert describe_policy(policy) == (
            "Lockout policy: locks after 5 bad passwords, stays locked for 30 min, "
            "bad password count resets after 30 min"
        )

    def test_describe_policy_until_admin_unlocks(self):
        assert "until an admin unlocks it" in describe_policy(LockoutPolicy(3, None, timedelta(hours=1)))

    def test_no_threshold(self):
        assert "never lock out" in describe_policy(LockoutPolicy(0, None, None))


def test_dc_statuses_sorted_most_recent_bad_password_first():
    now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    statuses = [
        DcLockoutStatus("dc-old", 1, now - timedelta(days=2)),
        DcLockoutStatus("dc-error", error="timed out"),
        DcLockoutStatus("dc-none", 0, None),
        DcLockoutStatus("dc-recent", 4, now),
    ]
    assert [s.dc for s in sort_dc_statuses(statuses)] == ["dc-recent", "dc-old", "dc-none", "dc-error"]


def test_per_dc_status_keeps_going_when_one_dc_fails(monkeypatch):
    client = ad_client.ADClient("dc01.test.local", "DC=test,DC=local")

    class FakeConn:
        user, password = "op@test.local", "pw"

    client._conn = FakeConn()

    def read(dc, user, password, user_dn):
        assert (user, password) == ("op@test.local", "pw")
        if dc == "dc2":
            return DcLockoutStatus(dc, error="unreachable")
        return DcLockoutStatus(dc, bad_password_count=1)

    monkeypatch.setattr(ad_client, "_read_dc_lockout_status", read)
    statuses = client.get_lockout_status_per_dc("CN=x", domain_controllers=["dc1", "dc2", "dc3"])
    assert [(s.dc, s.error) for s in statuses] == [("dc1", ""), ("dc2", "unreachable"), ("dc3", "")]


def test_per_dc_status_refused_when_signed_out():
    client = ad_client.ADClient("dc01.test.local", "DC=test,DC=local")
    with pytest.raises(ad_client.SessionLockedError):
        client.get_lockout_status_per_dc("CN=x", domain_controllers=["dc1"])

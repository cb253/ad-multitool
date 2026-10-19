from datetime import datetime, timezone

import pytest
from ldap3.operation.search import parse_filter

from ad_client import (
    account_expires_to_datetime,
    build_filter_value,
    cn_from_dn,
    container_from_dn,
    dns_domain_from_base_dn,
    filetime_to_datetime,
    generalized_time_to_datetime,
    qualify_server_name,
    rid_from_sid,
)
from net_tools import is_valid_target


class TestBuildFilterValue:
    def test_plain_term_is_wrapped_in_wildcards(self):
        assert build_filter_value("jdoe") == "*jdoe*"

    def test_explicit_wildcard_is_honored(self):
        assert build_filter_value("lap*") == "lap*"

    def test_ldap_special_characters_are_escaped(self):
        assert build_filter_value("x)(objectClass=user") == "*x\\29\\28objectClass=user*"

    def test_crafted_term_cannot_add_filter_clauses(self):
        # Parse the filter the way ldap3 sends it. An injection would show up as extra clauses.
        term = build_filter_value("x)(sAMAccountName=*")
        root = parse_filter(
            f"(sAMAccountName={term})", None, auto_escape=True, auto_encode=True, validator=None, check_names=False
        )
        (clause,) = root.elements
        assert clause.elements == []
        assert clause.assertion["attr"] == "sAMAccountName"
        assert clause.assertion["initial"] == b"x\\29\\28sAMAccountName="

    def test_backslash_is_escaped(self):
        assert build_filter_value("a\\b") == "*a\\5cb*"


class TestTimeConversion:
    def test_filetime_zero_is_none(self):
        assert filetime_to_datetime(0) is None
        assert filetime_to_datetime(None) is None

    def test_filetime_converts_to_utc(self):
        # 2024-01-01T00:00:00Z
        assert filetime_to_datetime(133485408000000000) == datetime(2024, 1, 1, tzinfo=timezone.utc)

    @pytest.mark.parametrize("value", [0, 0x7FFFFFFFFFFFFFFF, None])
    def test_account_expires_never(self, value):
        assert account_expires_to_datetime(value) is None

    def test_generalized_time(self):
        assert generalized_time_to_datetime("20240101120000.0Z") == datetime(2024, 1, 1, 12, tzinfo=timezone.utc)

    def test_generalized_time_invalid(self):
        assert generalized_time_to_datetime("garbage") is None


class TestDnHelpers:
    def test_cn_from_dn(self):
        assert cn_from_dn("CN=Jane Doe,OU=Users,DC=test,DC=local") == "Jane Doe"
        assert cn_from_dn("") == ""

    def test_container_from_dn(self):
        assert container_from_dn("CN=Jane Doe,OU=Users,DC=test,DC=local") == "OU=Users,DC=test,DC=local"

    def test_dns_domain_from_base_dn(self):
        assert dns_domain_from_base_dn("DC=example,DC=com") == "example.com"
        assert dns_domain_from_base_dn("OU=Users, DC=test, DC=local") == "test.local"


class TestQualifyServerName:
    def test_short_name_gets_domain(self):
        assert qualify_server_name("dc01", "DC=example,DC=com") == "dc01.example.com"

    @pytest.mark.parametrize("server", ["dc01.example.com", "10.0.0.5", "fe80::1"])
    def test_qualified_names_and_ips_unchanged(self, server):
        assert qualify_server_name(server, "DC=example,DC=com") == server

    def test_no_domain_in_base_dn(self):
        assert qualify_server_name("dc01", "") == "dc01"


def test_rid_from_sid():
    assert rid_from_sid("S-1-5-21-1-2-3-500") == 500
    assert rid_from_sid("") is None


class TestNetworkTarget:
    @pytest.mark.parametrize("target", ["10.0.0.1", "dc01", "dc01.example.com", "fe80::1%12"])
    def test_valid(self, target):
        assert is_valid_target(target)

    @pytest.mark.parametrize("target", ["", "-t", "-n 1 x", "host name", "a;calc", "a&b", "a|b"])
    def test_invalid(self, target):
        assert not is_valid_target(target)

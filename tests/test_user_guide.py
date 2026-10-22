import re

import pytest

from user_guide import SECTIONS

# Any domain-style name in user-facing text must be a documentation placeholder,
# never a real organization's server or domain.
DOMAIN_NAME = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|local|lan|corp)\b", re.IGNORECASE)
DC_COMPONENT = re.compile(r"DC=([^,`\s]+)", re.IGNORECASE)
ALLOWED_DOMAINS = {"example.com"}


def test_sections_have_titles_and_content():
    assert SECTIONS
    titles = [title for title, _ in SECTIONS]
    assert len(titles) == len(set(titles))
    for title, body in SECTIONS:
        assert title.strip()
        assert len(body.strip()) > 100


@pytest.mark.parametrize(("title", "body"), SECTIONS)
def test_only_placeholder_domains(title, body):
    for match in DOMAIN_NAME.finditer(body):
        domain = ".".join(match.group(0).lower().split(".")[-2:])
        assert domain in ALLOWED_DOMAINS, f"{title}: {match.group(0)}"
    for component in DC_COMPONENT.findall(body):
        assert component.lower() in {"example", "com"}, f"{title}: DC={component}"


@pytest.mark.parametrize(
    "body", ["Use `dc01.contoso.net` to sign in.", "Base DN `DC=acme,DC=com`.", "Mail jdoe@acme.org."]
)
def test_placeholder_check_catches_real_looking_names(body):
    with pytest.raises(AssertionError):
        test_only_placeholder_domains("sample", body)


@pytest.mark.parametrize(("title", "body"), SECTIONS)
def test_no_em_or_en_dashes(title, body):
    assert "—" not in body and "–" not in body

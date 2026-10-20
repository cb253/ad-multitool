from tabs.compare_base import Comparison, ComparisonRow


def _comparison():
    return Comparison(
        title="Group membership comparison: jdoe vs asmith",
        item_header="Group",
        name_a="jdoe",
        name_b="asmith",
        rows=[
            ComparisonRow("All", True, True),
            ComparisonRow("AP-IT", True, False),
            ComparisonRow("VPN", False, True, tooltip="Remote access"),
        ],
    )


def test_summary_counts_shared_and_unique():
    assert _comparison().summary == "1 shared | 1 only in jdoe | 1 only in asmith"


def test_copied_text_is_tab_separated_with_header():
    assert _comparison().as_text().splitlines() == [
        "Group membership comparison: jdoe vs asmith",
        "1 shared | 1 only in jdoe | 1 only in asmith",
        "",
        "Group\tjdoe\tasmith",
        "All\tYes\tYes",
        "AP-IT\tYes\tNo",
        "VPN\tNo\tYes",
    ]


def test_user_comparison_copies_only_unique_groups_per_person():
    from tabs.compare_users_tab import UserGroupsComparison

    comparison = UserGroupsComparison(
        **{**_comparison().__dict__},
        heading_a="Jane Doe (jdoe)",
        heading_b="Al Smith (asmith)",
    )
    assert comparison.as_text().splitlines() == [
        "Jane Doe (jdoe)",
        "AP-IT",
        "",
        "Al Smith (asmith)",
        "VPN",
    ]


def test_user_comparison_with_nothing_unique_says_none():
    from tabs.compare_users_tab import UserGroupsComparison

    comparison = UserGroupsComparison(
        title="t",
        item_header="Group",
        name_a="a",
        name_b="b",
        rows=[ComparisonRow("All", True, True)],
        heading_a="A (a)",
        heading_b="B (b)",
    )
    assert comparison.as_text().splitlines() == ["A (a)", "(none)", "", "B (b)", "(none)"]

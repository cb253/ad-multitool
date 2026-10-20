from dataclasses import dataclass

from PySide6.QtWidgets import QMessageBox

from tabs.compare_base import CompareTab, Comparison, ComparisonRow


@dataclass
class UserGroupsComparison(Comparison):
    heading_a: str = ""
    heading_b: str = ""

    def as_text(self):
        """Each person, then the groups only they have. Shared groups are left out."""
        only_a = [row.name for row in self.rows if row.in_a and not row.in_b]
        only_b = [row.name for row in self.rows if row.in_b and not row.in_a]
        return "\n".join([self.heading_a, *(only_a or ["(none)"]), "", self.heading_b, *(only_b or ["(none)"])])


class CompareUsersTab(CompareTab):
    LABEL_A = "User A"
    LABEL_B = "User B"
    PLACEHOLDER_A = "Username A"
    PLACEHOLDER_B = "Username B"
    ITEM_HEADER = "Group"

    def fetch(self, client, name_a, name_b):
        user_a = client.get_user(name_a)
        user_b = client.get_user(name_b)
        groups_a = client.get_effective_groups_with_descriptions(user_a.distinguished_name) if user_a else []
        groups_b = client.get_effective_groups_with_descriptions(user_b.distinguished_name) if user_b else []
        return user_a, user_b, groups_a, groups_b

    def build_comparison(self, name_a, name_b, result):
        user_a, user_b, groups_a, groups_b = result
        if user_a is None or user_b is None:
            missing = name_a if user_a is None else name_b
            QMessageBox.warning(self, "User not found", f"Could not find account '{missing}'")
            return None

        descriptions = dict(groups_a + groups_b)
        names_a = {name for name, _ in groups_a}
        names_b = {name for name, _ in groups_b}
        rows = [
            ComparisonRow(group, group in names_a, group in names_b, descriptions[group] or "No description")
            for group in sorted(names_a | names_b, key=str.lower)
        ]
        return UserGroupsComparison(
            title=f"Group membership comparison: {user_a.sam_account_name} vs {user_b.sam_account_name}",
            item_header=self.ITEM_HEADER,
            name_a=user_a.sam_account_name,
            name_b=user_b.sam_account_name,
            rows=rows,
            heading_a=f"{user_a.common_name} ({user_a.sam_account_name})",
            heading_b=f"{user_b.common_name} ({user_b.sam_account_name})",
        )

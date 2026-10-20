from PySide6.QtWidgets import QMessageBox

from ad_client import cn_from_dn
from tabs.compare_base import CompareTab, Comparison, ComparisonRow


class CompareGroupsTab(CompareTab):
    LABEL_A = "Group A DN"
    LABEL_B = "Group B DN"
    PLACEHOLDER_A = "CN=GroupA,OU=Groups,DC=example,DC=com"
    PLACEHOLDER_B = "CN=GroupB,OU=Groups,DC=example,DC=com"
    ITEM_HEADER = "Username"

    def fetch(self, client, dn_a, dn_b):
        return (
            client.get_group_members(dn_a),
            client.get_group_members(dn_b),
            client.get_group_description(dn_a),
            client.get_group_description(dn_b),
        )

    def build_comparison(self, dn_a, dn_b, result):
        members_a, members_b, description_a, description_b = result
        self.edit_a.setToolTip(description_a or "No description")
        self.edit_b.setToolTip(description_b or "No description")

        if members_a.truncated or members_b.truncated:
            QMessageBox.warning(
                self,
                "Results truncated",
                "The directory server did not return every member, so this comparison is incomplete.",
            )

        names_a = {user.sam_account_name: user.common_name for user in members_a.items}
        names_b = {user.sam_account_name: user.common_name for user in members_b.items}
        display_names = {**names_a, **names_b}
        rows = [
            ComparisonRow(sam, sam in names_a, sam in names_b, display_names[sam])
            for sam in sorted(display_names, key=str.lower)
        ]
        group_a, group_b = cn_from_dn(dn_a), cn_from_dn(dn_b)
        return Comparison(
            title=f"Group member comparison: {group_a} vs {group_b}",
            item_header=self.ITEM_HEADER,
            name_a=group_a,
            name_b=group_b,
            rows=rows,
        )

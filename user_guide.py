"""User Guide content shown under Help > User Guide.

Kept as Python strings (not a data file) so the packaged exe needs no extra bundling.
Each section is (title, markdown). Use generic examples only, never real server or domain names.
"""

SECTIONS = [
    (
        "Signing In",
        """
## Signing In

The tool signs in to Active Directory with your own account. What you can do is limited to what that account is allowed to do in AD.

Fill in the sign-in window:

- **Server.** The domain controller's full name, for example `dc01.example.com`. A short name such as `dc01` is completed using the Base DN.
- **Base DN.** Where searches start, for example `DC=example,DC=com`.
- **UPN Suffix / Domain.** Added to your username when you sign in, for example `example.com`.
- **Username** and **Password.** Your AD account.

Everything except the password is remembered for next time. The password is never saved.

The tool connects only over LDAPS (port 636) and checks the domain controller's certificate.

**If you see a certificate warning**, do not try to work around it. Check that the server name matches the name on the domain controller's certificate. Connecting by IP address always fails this check. If the name is correct and the warning persists, the connection may be intercepted. Stop and contact your domain administrator.
""",
    ),
    (
        "Searching",
        """
## Searching

The **Search** tab finds users and computers from one box.

- Users match on username, display name, or email.
- Computers match on computer name or DNS hostname.
- Type part of a name to match anywhere in it. Use `*` to control matching. For example `lap*` finds names that start with "lap".

Results appear in one list. The **Type** column shows User or Computer.

Select a result to see its details below the list.

- **A user** shows account details on the left and **Group Memberships** on the right. Groups include nested membership. Hover a group to see its description.
- **A computer** shows its hostname, operating system, OU, and last logon.
- **Several results** show a short summary instead.

If a search returns too many matches, a warning asks you to narrow it.
""",
    ),
    (
        "Unlocking and Disabling",
        """
## Unlocking and Disabling Accounts

The green **Unlock Selected** and red **Disable Selected** buttons appear when at least one user is selected. They act on the selected users only. Selected computers are ignored.

**To unlock** one or more accounts:

1. Select the users in the results list.
2. Click **Unlock Selected**.
3. Confirm the list of accounts.

**To disable** one or more accounts:

1. Select the users.
2. Click **Disable Selected**.
3. Review the confirmation. It lists any accounts that will be skipped and why.

The tool refuses to disable these accounts:

- the account you are signed in with
- the built-in Administrator and krbtgt accounts
- members of Domain Admins, Schema Admins, Enterprise Admins, or Administrators
- protected admin accounts (marked with adminCount)

Use Active Directory Users and Computers if disabling one of these is intentional.

Every unlock and disable is recorded in the local audit log, including failed and refused attempts. See **Logs and Settings**.
""",
    ),
    (
        "Lockout History",
        """
## Lockout History

Select one user and click **Lockout History** under the account details. A separate window opens.

**Lockout policy.** The top of the window shows how many bad passwords lock an account and how long it stays locked.

**Per domain controller.** Each domain controller counts bad passwords separately. The table lists every domain controller with its bad password count, last bad password, last logon, and lockout time. The domain controller with the most recent bad password is listed first. It usually points to where the bad attempts came from.

**Lockout events.** These are read from the domain controller that records every lockout in the domain (the PDC emulator).

- **Locked out** rows show the **caller computer**, the machine that sent the bad passwords.
- **Unlocked** rows show who unlocked the account.
- The note below the table shows how far back the history goes.

Reading lockout events requires your account to be in the **Event Log Readers** group on the domain controllers. If it is not, the window explains what to ask your domain administrator for. The per domain controller table still works without it.

Click **Copy Results** to paste the whole report into a ticket. Click **Refresh** to load it again.
""",
    ),
    (
        "Comparing Users",
        """
## Comparing Users

Use **Compare Users** to see how two accounts' group memberships differ.

1. Enter two usernames.
2. Click **Compare** or press Enter.

The table lists every group either user is in, including nested groups.

- **Green rows** are groups both users share.
- **Red rows** are groups only one user has. In those rows, the **Yes** cell is green so you can see which user has it.
- Hover any row to see the group's description.

The summary line counts shared groups and groups unique to each user.

**Copy Results** copies each user's name followed by the groups only they have. Shared groups are left out. This is handy for access requests.
""",
    ),
    (
        "Comparing Groups",
        """
## Comparing Groups

Use **Compare Groups** to see which users two groups share.

1. Enter each group's distinguished name, for example `CN=Sales,OU=Groups,DC=example,DC=com`.
2. Click **Compare** or press Enter.

The table lists every member of either group, including members through nested groups. Colors work the same way as Compare Users.

- Hover a group's name box to see the group's description.
- Hover a row to see the user's display name.

**Copy Results** copies the full table as tab-separated text. It pastes into Excel as columns.
""",
    ),
    (
        "Networking",
        """
## Networking

Use **Networking** to run common network checks without opening a command prompt.

Enter an IP address or computer name as the target, then choose a command.

- **Ping.** Sends four pings. Pressing Enter in the target box also runs this.
- **Continuous Ping.** Pings until you click **Stop**.
- **Traceroute.** Shows each network hop to the target.
- **NSLookup.** Looks up the target in DNS.
- **Test Port.** Checks whether a TCP port is open, with a 3 second timeout. The port defaults to 3389 (Remote Desktop).

Output from each run is added below the previous one. Click **Clear** to empty it.

Targets must be a hostname or IP address. Spaces and command symbols are rejected.
""",
    ),
    (
        "Session Lock",
        """
## Session Lock

After 10 minutes with no keyboard or mouse activity, the tool locks itself.

When it locks:

- it signs out of Active Directory and forgets your password
- every tool window is hidden
- any confirmation that was open is cancelled

To continue, enter your password and click **Unlock**. Your windows come back as they were. Click **Sign Out** to close the tool instead.

A continuous ping keeps running while locked. Its output is still there after you unlock.
""",
    ),
    (
        "Logs and Settings",
        """
## Logs and Settings

The tool keeps its files in your Windows profile, not on a server. Open the folder with **Help > Open Logs Folder**.

- **audit.log.** Every unlock and disable, with who did it, when, and whether it succeeded, failed, or was refused.
- **app.log.** Errors and diagnostic details. Useful when reporting a problem.
- **settings.json.** The sign-in details remembered for next time.

Your password is never written to any of these files.

The audit log is a convenience record kept on your computer. The domain controllers' own security logs remain the official record of account changes.
""",
    ),
    (
        "Troubleshooting",
        """
## Troubleshooting

**"The certificate presented by ... could not be verified"**
Use the domain controller's full name exactly as it appears on its certificate. Do not use an IP address. If the name is correct, contact your domain administrator.

**"Could not reach ... on port 636"**
The domain controller is offline, the name is wrong, or a firewall blocks LDAPS. Try another domain controller or check the network.

**"Invalid username or password"**
Check the username and the UPN Suffix / Domain field. Repeated failures can lock your own account.

**Lockout events show "Access ... was denied"**
Your account is not in the **Event Log Readers** group on the domain controllers. Ask your domain administrator. The window shows exactly what to request.

**Something unexpected happened**
Open **Help > Open Logs Folder** and check **app.log** for the details. Include it when reporting a problem.
""",
    ),
]

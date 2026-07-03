# AD Multitool

Desktop GUI for common Active Directory administration tasks: viewing and comparing group membership, searching for and unlocking accounts, and checking last logins. Built for a small internal IT team to reduce the need to jump into ADUC or PowerShell for routine lookups.

Initial version built July 2026.

## What it does

- **Search & Unlock**: search for user accounts by username, name, or email (`*` wildcards supported), view full account details (title, department, description, creation date, password last set, account expiration, nested group membership), and unlock locked-out accounts with a confirmation prompt.
- **Computers**: search for computer accounts by name or DNS hostname, and view operating system, description, and last logon.
- **Compare Groups**: compare two AD groups (including nested membership) and see which users are shared vs. unique to each.
- **Compare Users**: compare two user accounts' group memberships side by side, shared groups highlighted in green, differing groups in red.
- Local audit log of every account unlock (who, what, when).
- Non-secret connection settings (server, base DN, domain) are remembered between launches. Passwords are never stored.

## Requirements

- Windows
- Python 3.12+ (only needed to run from source; the packaged `.exe` has no dependencies)

## Running from source

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

Or double-click `run.bat` once the virtual environment is set up.

## Building a standalone executable

```
.venv\Scripts\pip install -r requirements-dev.txt
build.bat
```

The output lands in `dist\AD-Multitool\`. Copy the whole folder to distribute it. The `.exe` depends on the files next to it, so it can't be copied out on its own.

## Data storage

Settings and the audit log are stored per-user in `%APPDATA%\AD-Multitool\`, not in the application folder. This keeps the tool working correctly even when run from a shared, read-only network location.

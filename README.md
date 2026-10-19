# AD Multitool

Desktop GUI for common Active Directory administration tasks: viewing and comparing group membership, searching for and unlocking accounts, and checking last logins. Built for a small internal IT team to reduce the need to jump into ADUC or PowerShell for routine lookups.

Initial version built July 2026.

## What it does

- **Search & Unlock**: search for user accounts by username, name, or email (`*` wildcards supported), view full account details (title, department, description, creation date, password last set, account expiration, nested group membership), and unlock or disable accounts, each with a confirmation prompt.
- **Computers**: search for computer accounts by name or DNS hostname, and view operating system, description, and last logon.
- **Compare Users**: compare two user accounts' group memberships side by side, shared groups highlighted in green, differing groups in red.
- **Compare Groups**: compare two AD groups (including nested membership) and see which users are shared vs. unique to each.
- **Networking**: run ping, continuous ping, traceroute, nslookup, or a TCP port test against an IP address or computer name, with live output and a Stop button.
- Local audit log of every account unlock and disable, including failed and refused attempts (who, what, when, outcome).
- Non-secret connection settings (server, base DN, domain, username) are remembered between launches. Passwords are never stored.

## Requirements

- Windows
- Python 3.12+ (only needed to run from source; the packaged `.exe` has no dependencies)

## Running from source

```
python -m venv .venv
.venv\Scripts\pip install --require-hashes -r requirements.txt
.venv\Scripts\python main.py
```

Or double-click `run.bat` once the virtual environment is set up.

## Building a standalone executable

```
.venv\Scripts\pip install --require-hashes -r requirements-dev.txt
build.bat
```

The output lands in `dist\AD-Multitool\`. Copy the whole folder to distribute it. The `.exe` depends on the files next to it, so it can't be copied out on its own.

The app version lives in `version.py`. It is shown in the window title and in the exe's file properties. Bump it for every release.

To code-sign the exe, set `SIGN_CERT_THUMBPRINT` to the thumbprint of a code-signing certificate in your user certificate store before running `build.bat`. `signtool` (Windows SDK) must be on the PATH. Without the variable, the build finishes unsigned.

## Development

```
.venv\Scripts\ruff check .
.venv\Scripts\ruff format .
.venv\Scripts\python -m pytest
.venv\Scripts\pip-audit -r requirements.txt --require-hashes --disable-pip
```

CI (`.github/workflows/ci.yml`) runs the same checks on every push.

Dependencies are pinned with hashes. To change one, edit `requirements.in` or `requirements-dev.in`, then regenerate the matching `.txt` with `.venv\Scripts\pip-compile --generate-hashes --allow-unsafe <file>.in`.

## Data storage

Settings, the audit log, and the application error log (`app.log`) are stored per-user in `%APPDATA%\AD-Multitool\`, not in the application folder. This keeps the tool working correctly even when run from a shared, read-only network location.

## Security

- **Connection.** The tool connects only over LDAPS (port 636) and validates the domain controller's certificate against the Windows trusted root store. Enter the DC by its full name as it appears on the certificate (e.g. `dc01.example.com`). A short name is expanded using the Base DN. Connecting by IP address fails validation by design. A certificate error at sign-in can mean the connection is being intercepted. Do not work around it.
- **Permissions.** The tool holds no service account. Every action runs with the signed-in operator's own AD rights, so what an operator can do is controlled by AD delegation. Grant operators only "Read lockoutTime / Write lockoutTime" and "Write userAccountControl" on the OUs they support, not Domain Admins.
- **Disable guardrails.** The tool refuses to disable the signed-in account, the built-in Administrator and krbtgt accounts, members of Domain Admins, Schema Admins, Enterprise Admins, or Administrators (matched by SID, so renamed groups are still caught), and any account with `adminCount=1`. Use ADUC when one of these is intentional.
- **Audit.** The local audit log is a convenience record in the operator's own profile and can be edited by them. The authoritative record is the domain controllers' Security log. Event 4767 records unlocks and event 4725 records disables, with the operator's account as the subject.
- **Distribution.** Run the exe from a share that operators can read but not write, so the exe cannot be replaced. Sign release builds (see above).

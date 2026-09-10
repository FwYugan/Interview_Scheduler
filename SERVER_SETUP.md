# Ubuntu Server Setup

The project uses Python, SQLite, and Caddy. The bootstrap script installs the
following server packages:

- Caddy (official stable package)
- Git
- Python virtual-environment support and pip
- SQLite command-line tools

Run the first command in the **local VS Code PowerShell terminal** to upload the script.
The absolute path avoids errors when the terminal is not currently in the project directory:

```powershell
scp C:\Users\tjhdy\Desktop\Project_1\scripts\bootstrap_ubuntu.sh ubuntu@82.156.112.198:/tmp/bootstrap_ubuntu.sh
```

Then connect to the server. The prompt should change from `PS C:\\...>` to an
Ubuntu prompt such as `ubuntu@server:~$`:

```powershell
ssh ubuntu@82.156.112.198
```

After the SSH login succeeds, run the following command in the **Ubuntu SSH terminal**:

```bash
sudo bash /tmp/bootstrap_ubuntu.sh
```

Do not run the `sudo` command at a `PS C:\\...>` prompt; that is the local
Windows terminal, where `sudo` and `bash` are normally unavailable.

Verify the installation after it finishes:

```bash
python3 --version
git --version
sqlite3 --version
caddy version
systemctl status caddy --no-pager
```

For the public site, open TCP ports 80 and 443 in both the cloud security group
and the server firewall, if one is enabled. Port 443 will be needed when a
domain name is connected and Caddy obtains a trusted TLS certificate.

The script does not change firewall, SSH, users, or the Caddy site
configuration. Those will be configured once the application exists.

# Pterodactyl Backup Tool

A tool that connects to a [Pterodactyl](https://pterodactyl.io/) panel through its Client API and creates a local backup of every server it can access: full file tree plus a SQL dump of each attached database.

Designed for self-hosted setups where you want an off-VPS copy of your data on a local machine, in case the server becomes unreachable.

## Features

- Downloads the complete file tree of every server visible to the API key, recursively.
- Dumps each Pterodactyl-managed database to a standalone `.sql` file.
- Configurable exclude patterns (e.g. `node_modules`, `.git`, build artifacts) to skip files that don't need backing up.
- Automatic retry with backoff on network errors and API rate limits.
- One folder per server, ready to browse or restore from.
- Terminal UI with live transfer progress and a summary table at the end of the run.

## Requirements

- Python 3.8 or later
- A Pterodactyl panel with API access enabled
- A **Client API key** (not an Application API key — see [Getting an API key](#getting-an-api-key))

## Installation

Clone the repository and install the dependencies:

```bash
git clone https://github.com/ayka-667/PteroBackup.git
cd PteroBackup
pip install -r requirements.txt
```

Or, with the dependencies installed directly:

```bash
pip install requests pymysql rich
```

## Getting an API key

1. Log in to your Pterodactyl panel.
2. Open **Account Settings** (top right) → **API Credentials**.
3. Create a new key and copy the value shown — it starts with `ptlc_` and will not be displayed again.

This must be a **Client API** key, created from your account settings. **Application API** keys (created from the Admin panel) are a different, higher-privilege credential and will be rejected by the endpoints this tool uses, with a `403 Forbidden` response.

## Configuration

On first run, the script generates a `config.json` file in the working directory and exits so you can fill it in:

```bash
python main.py
```

Edit `config.json`:

```json
{
    "panel_url": "https://panel.yourdomain.com",
    "api_key": "ptlc_xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "backup_destination": "C:/Backups/Pterodactyl",
    "request_timeout": 30,
    "retry_count": 3,
    "retry_delay": 5,
    "rate_limit_delay": 0.3,
    "database_connect_timeout": 10,
    "skip_files": false,
    "skip_databases": false,
    "exclude_patterns": [
        "node_modules", ".next", ".nuxt", ".npm", "alpine", ".local",
        ".git", ".svn", "__pycache__", "*.pyc", ".venv", "venv",
        "vendor", "target", "dist", "build", ".cache", ".turbo",
        ".parcel-cache", "*.log", "logs"
    ]
}
```

## Usage

```bash
python pterodactyl_backup.py
```

For each server the API key can access, the tool creates:

```
backup_destination/
└── <server-name>/
    ├── files/
    └── <database-name>.sql
```

Each run overwrites existing files in the destination. There is no incremental sync — every run re-downloads the full file tree.

## How database dumps work

Each server's databases are listed through the Client API, which also returns connection credentials. The tool then connects directly to that MySQL host and writes a SQL dump (`DROP TABLE` + `CREATE TABLE` + `INSERT` statements) without depending on a local `mysqldump` binary.

This requires the MySQL host to be reachable from the machine running the script. On many Pterodactyl setups, the database host is only bound to the VPS's internal network, in which case the connection will fail with a clear error. To fix this:

- Open the relevant MySQL port (commonly `3306`) on the node's firewall for your IP, **and**
- Make sure the database's `connections_from` setting in Pterodactyl permits that IP as well.

If exposing the database port isn't an option, dumping from inside the VPS (via the server console, then downloading the resulting file) is a safer alternative, though it isn't implemented here.

## Limitations

- No incremental sync or deduplication; every run is a full copy.
- The SQL dump is a straightforward schema-plus-data export. It does not capture triggers, views, or stored procedures.
- File transfer goes through the Client API's per-file download endpoints rather than SFTP, which is simpler to set up but slower for large file counts.
- Subject to the panel's API rate limit (240 requests/minute per key by default in Pterodactyl).

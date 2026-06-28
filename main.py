import os
import sys
import json
import time
import fnmatch
import requests
import pymysql
from pathlib import Path
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn,
    TimeElapsedColumn, DownloadColumn, TransferSpeedColumn,
)
from rich.text import Text
from rich.align import Align
from rich import box

ACCENT = "#5f87af"
ACCENT_DIM = "#3a526b"
MUTED = "grey62"
OK = "#6a9955"
WARN = "#b58900"
FAIL = "#a85c5c"
TEXT = "grey85"

console = Console()

CONFIG_FILE = "config.json"

DEFAULT_EXCLUDE_PATTERNS = [
    "node_modules",
    ".next",
    ".nuxt",
    ".npm",
    "alpine",
    ".local",
    ".git",
    ".svn",
    "__pycache__",
    "*.pyc",
    ".venv",
    "venv",
    "vendor",
    "target",
    "dist",
    "build",
    ".cache",
    ".turbo",
    ".parcel-cache",
    "*.log",
    "logs"
]

DEFAULT_CONFIG = {
    "panel_url": "https://panel.example.com",
    "api_key": "ptlc_xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "backup_destination": "C:/Backups/Pterodactyl",
    "request_timeout": 30,
    "retry_count": 3,
    "retry_delay": 5,
    "rate_limit_delay": 0.3,
    "database_connect_timeout": 10,
    "skip_files": False,
    "skip_databases": False,
    "exclude_patterns": DEFAULT_EXCLUDE_PATTERNS
}


def load_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
        console.print(f"[{WARN}]No '{CONFIG_FILE}' file found. An example has been created.[/{WARN}]")
        console.print(f"[{WARN}]Fill it in (panel_url, api_key, backup_destination) then run the script again.[/{WARN}]")
        sys.exit(1)

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    for key, value in DEFAULT_CONFIG.items():
        config.setdefault(key, value)

    if "panel.example.com" in config["panel_url"] or "ptlc_xxx" in config["api_key"]:
        console.print(f"[{FAIL}]Please configure '{CONFIG_FILE}' with your real panel_url and api_key.[/{FAIL}]")
        sys.exit(1)

    config["panel_url"] = config["panel_url"].rstrip("/")
    return config


CONFIG = load_config()

SESSION = requests.Session()
SESSION.headers.update({
    "Authorization": f"Bearer {CONFIG['api_key']}",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
})

def api_get(path, params=None, silent=False):
    url = f"{CONFIG['panel_url']}/api/client{path}"
    last_error = None

    for attempt in range(1, CONFIG["retry_count"] + 1):
        try:
            resp = SESSION.get(url, params=params, timeout=CONFIG["request_timeout"])

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", CONFIG["retry_delay"]))
                if not silent:
                    console.print(f"    [{WARN}]Rate limit hit, waiting {wait}s...[/{WARN}]")
                time.sleep(wait)
                continue

            if resp.status_code == 401:
                console.print(f"[bold {FAIL}]Error 401[/bold {FAIL}]: your API key is invalid or expired.")
                sys.exit(1)

            if resp.status_code == 403:
                console.print(f"[bold {FAIL}]Error 403 (Forbidden)[/bold {FAIL}] on {url}")
                console.print(f"    Response body: {resp.text[:500]}")
                console.print("    Possible causes: WAF/Cloudflare in front of the panel, an")
                console.print("    IP-restricted API key, or a disabled key. See the README.")
                return None

            if resp.status_code == 404:
                return None

            resp.raise_for_status()
            time.sleep(CONFIG["rate_limit_delay"])
            return resp.json()

        except requests.exceptions.RequestException as e:
            last_error = e
            if not silent:
                console.print(f"    [{WARN}]Network error (attempt {attempt}/{CONFIG['retry_count']}): {e}[/{WARN}]")
            time.sleep(CONFIG["retry_delay"])

    console.print(f"    [bold {FAIL}]Giving up[/bold {FAIL}] on {url} after {CONFIG['retry_count']} attempts: {last_error}")
    return None


def sanitize_name(name):
    invalid_chars = '<>:"/\\|?*'
    for ch in invalid_chars:
        name = name.replace(ch, "_")
    return name.strip() or "unnamed"


def is_excluded(name):
    patterns = CONFIG.get("exclude_patterns", [])
    name_lower = name.lower()
    for pattern in patterns:
        if fnmatch.fnmatch(name_lower, pattern.lower()):
            return True
    return False


def human_size(num_bytes):
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024

def get_all_servers():
    servers = []
    page = 1
    while True:
        data = api_get("", params={"page": page, "per_page": 50})
        if not data or "data" not in data:
            break

        servers.extend(data["data"])

        pagination = data.get("meta", {}).get("pagination", {})
        if page >= pagination.get("total_pages", 1):
            break
        page += 1

    return servers

def list_directory(server_id, directory="/"):
    data = api_get(f"/servers/{server_id}/files/list", params={"directory": directory})
    if not data:
        return []
    return data.get("data", [])


def get_download_url(server_id, file_path):
    data = api_get(f"/servers/{server_id}/files/download", params={"file": file_path})
    if not data:
        return None
    return data.get("attributes", {}).get("url")


def download_file(url, local_path, progress, task_id):
    try:
        with SESSION.get(url, stream=True, timeout=CONFIG["request_timeout"]) as resp:
            resp.raise_for_status()
            local_path.parent.mkdir(parents=True, exist_ok=True)
            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)
                        progress.update(task_id, advance=len(chunk))
        return True
    except requests.exceptions.RequestException as e:
        console.print(f"      [{FAIL}]Failed to download '{local_path.name}'[/{FAIL}]: {e}")
        return False


def collect_file_tree(server_id, remote_dir, local_dir, stats, file_list):
    entries = list_directory(server_id, remote_dir)

    for entry in entries:
        attrs = entry.get("attributes", {})
        name = attrs.get("name")
        is_file = attrs.get("is_file", True)

        if is_excluded(name):
            stats["excluded"] += 1
            continue

        remote_path = f"{remote_dir.rstrip('/')}/{name}"
        local_path = local_dir / name

        if is_file:
            size = attrs.get("size", 0) or 0
            file_list.append((remote_path, local_path, size))
        else:
            local_path.mkdir(parents=True, exist_ok=True)
            collect_file_tree(server_id, remote_path, local_path, stats, file_list)


def backup_server_files(server_id, files_dir, stats):
    file_list = []

    with console.status(f"[{ACCENT}]Scanning file tree...[/{ACCENT}]", spinner="dots"):
        collect_file_tree(server_id, "/", files_dir, stats, file_list)

    if not file_list:
        console.print(f"    [{MUTED}]No files to download (or everything excluded).[/{MUTED}]")
        return

    total_size = sum(size for _, _, size in file_list)

    with Progress(
        SpinnerColumn(style=ACCENT),
        TextColumn(f"[{TEXT}]{{task.fields[filename]}}[/{TEXT}]"),
        BarColumn(complete_style=ACCENT, finished_style=ACCENT_DIM),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task_id = progress.add_task(
            "download", filename="...", total=total_size if total_size > 0 else None
        )

        for remote_path, local_path, size in file_list:
            progress.update(task_id, filename=local_path.name)
            url = get_download_url(server_id, remote_path)
            if url:
                ok = download_file(url, local_path, progress, task_id)
                if ok:
                    stats["files_ok"] += 1
                else:
                    stats["files_failed"] += 1
                    if size:
                        progress.update(task_id, advance=size)  # keep the bar consistent
            else:
                console.print(f"      [{WARN}]Download URL unavailable for {remote_path}[/{WARN}]")
                stats["files_failed"] += 1
                if size:
                    progress.update(task_id, advance=size)

def get_server_databases(server_id):
    data = api_get(f"/servers/{server_id}/databases", params={"include": "password"})
    if not data:
        return []
    return data.get("data", [])


def format_sql_value(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return "0x" + value.hex()
    text = str(value)
    text = text.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"


def dump_database(db_attrs, output_path):
    host = db_attrs["host"]["address"]
    port = db_attrs["host"]["port"]
    db_name = db_attrs["name"]
    username = db_attrs["username"]
    password = db_attrs.get("relationships", {}).get("password", {}).get("attributes", {}).get("password")

    if not password:
        console.print(f"      [{WARN}]Password not available for database '{db_name}', cannot dump.[/{WARN}]")
        return False

    try:
        conn = pymysql.connect(
            host=host,
            port=port,
            user=username,
            password=password,
            database=db_name,
            connect_timeout=CONFIG["database_connect_timeout"],
            charset="utf8mb4",
        )
    except pymysql.err.OperationalError as e:
        console.print(f"      [bold {FAIL}]Could not connect to MySQL[/bold {FAIL}] ({host}:{port}): {e}")
        console.print(f"      [{MUTED}]The Pterodactyl MySQL host is likely internal to the VPS and[/{MUTED}]")
        console.print(f"      [{MUTED}]not reachable from outside. See the README to open access.[/{MUTED}]")
        return False

    try:
        with conn.cursor() as cursor:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"-- Dump of database '{db_name}' generated on {datetime.now().isoformat()}\n")
                f.write("SET FOREIGN_KEY_CHECKS=0;\n\n")

                cursor.execute("SHOW TABLES;")
                tables = [row[0] for row in cursor.fetchall()]

                for table in tables:
                    cursor.execute(f"SHOW CREATE TABLE `{table}`;")
                    create_stmt = cursor.fetchone()[1]
                    f.write(f"-- ----------------------------\n")
                    f.write(f"-- Table: {table}\n")
                    f.write(f"-- ----------------------------\n")
                    f.write(f"DROP TABLE IF EXISTS `{table}`;\n")
                    f.write(f"{create_stmt};\n\n")

                    cursor.execute(f"SELECT * FROM `{table}`;")
                    rows = cursor.fetchall()
                    if rows:
                        col_names = [desc[0] for desc in cursor.description]
                        f.write(f"-- Data for {table}\n")
                        for row in rows:
                            values = ", ".join(format_sql_value(v) for v in row)
                            cols = ", ".join(f"`{c}`" for c in col_names)
                            f.write(f"INSERT INTO `{table}` ({cols}) VALUES ({values});\n")
                        f.write("\n")

                f.write("SET FOREIGN_KEY_CHECKS=1;\n")
        return True

    except Exception as e:
        console.print(f"      [bold {FAIL}]Error while dumping '{db_name}'[/bold {FAIL}]: {e}")
        return False
    finally:
        conn.close()

def backup_server(server, destination_root):
    attrs = server["attributes"]
    server_id = attrs["identifier"]
    server_name = sanitize_name(attrs["name"])

    console.print()
    console.rule(f"[bold {TEXT}]{attrs['name']}[/bold {TEXT}] [{MUTED}]({server_id})[/{MUTED}]", style=ACCENT_DIM)

    server_dir = destination_root / server_name
    server_dir.mkdir(parents=True, exist_ok=True)

    stats = {"files_ok": 0, "files_failed": 0, "excluded": 0, "db_ok": 0, "db_failed": 0}

    if not CONFIG["skip_files"]:
        files_dir = server_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        backup_server_files(server_id, files_dir, stats)

        summary = Text()
        summary.append("  ✓ ", style=OK)
        summary.append(f"{stats['files_ok']} file(s) ok", style=OK)
        if stats["files_failed"]:
            summary.append("   ✗ ", style=FAIL)
            summary.append(f"{stats['files_failed']} failed", style=FAIL)
        if stats["excluded"]:
            summary.append("   ⊘ ", style=MUTED)
            summary.append(f"{stats['excluded']} excluded", style=MUTED)
        console.print(summary)

    if not CONFIG["skip_databases"]:
        with console.status(f"[{ACCENT}]Looking up databases...[/{ACCENT}]", spinner="dots"):
            databases = get_server_databases(server_id)

        if not databases:
            console.print(f"  [{MUTED}]No Pterodactyl database for this server.[/{MUTED}]")
        else:
            for db in databases:
                db_attrs = db["attributes"]
                db_name = sanitize_name(db_attrs["name"])
                sql_path = server_dir / f"{db_name}.sql"
                with console.status(f"[{ACCENT}]Dumping database '{db_attrs['name']}'...[/{ACCENT}]", spinner="dots"):
                    ok = dump_database(db_attrs, sql_path)
                if ok:
                    size = sql_path.stat().st_size if sql_path.exists() else 0
                    console.print(f"  [{OK}]✓[/{OK}] Dumped [bold {TEXT}]{db_attrs['name']}[/bold {TEXT}] → "
                                  f"{sql_path.name} [{MUTED}]({human_size(size)})[/{MUTED}]")
                    stats["db_ok"] += 1
                else:
                    stats["db_failed"] += 1

    return stats


def print_banner():
    subtitle = Text("Server files & databases · local backup", style=MUTED, justify="center")
    console.print(Panel(
        subtitle,
        title=f"[bold {TEXT}]PTERODACTYL BACKUP TOOL[/bold {TEXT}]",
        title_align="center",
        border_style=ACCENT_DIM,
        box=box.ROUNDED,
        padding=(0, 2),
    ))


def print_summary_table(results, elapsed, destination_root):
    table = Table(
        title="Backup summary",
        box=box.SIMPLE_HEAVY,
        show_lines=False,
        title_style=f"bold {TEXT}",
        header_style=f"{MUTED}",
        border_style=ACCENT_DIM,
    )
    table.add_column("Server", style=TEXT)
    table.add_column("Files ok", justify="right", style=OK)
    table.add_column("Failed", justify="right", style=FAIL)
    table.add_column("Excluded", justify="right", style=MUTED)
    table.add_column("Database", justify="right", style=TEXT)

    total_ok = total_failed = total_excluded = total_db_ok = total_db_failed = 0

    for name, stats in results:
        db_parts = []
        if stats["db_ok"]:
            db_parts.append(f"{stats['db_ok']} ok")
        if stats["db_failed"]:
            db_parts.append(f"{stats['db_failed']} failed")
        db_str = " / ".join(db_parts) if db_parts else "-"

        table.add_row(
            name,
            str(stats["files_ok"]),
            str(stats["files_failed"]) if stats["files_failed"] else "-",
            str(stats["excluded"]) if stats["excluded"] else "-",
            db_str,
        )

        total_ok += stats["files_ok"]
        total_failed += stats["files_failed"]
        total_excluded += stats["excluded"]
        total_db_ok += stats["db_ok"]
        total_db_failed += stats["db_failed"]

    console.print()
    console.print(Align.center(table))

    footer = Text(justify="center")
    footer.append("Done in ", style=MUTED)
    footer.append(f"{elapsed:.1f}s", style=TEXT)
    footer.append("  ·  ", style=MUTED)
    footer.append(f"{len(results)} server(s)", style=TEXT)
    footer.append("  ·  ", style=MUTED)
    footer.append(f"{total_ok} files", style=OK)
    if total_failed:
        footer.append(f", {total_failed} failed", style=FAIL)
    console.print(Align.center(footer))

    path_line = Text(justify="center")
    path_line.append("Backups saved to: ", style=MUTED)
    path_line.append(str(destination_root.resolve()), style=f"underline {TEXT}")
    console.print(Align.center(path_line))


def main():
    print_banner()

    destination_root = Path(CONFIG["backup_destination"])
    destination_root.mkdir(parents=True, exist_ok=True)

    info = Table.grid(padding=(0, 1))
    info.add_column(style=MUTED, justify="right")
    info.add_column(style=TEXT)
    info.add_row("Panel", CONFIG["panel_url"])
    info.add_row("Output", str(destination_root.resolve()))
    console.print(Align.center(info))
    console.print()

    with console.status(f"[{ACCENT}]Fetching server list...[/{ACCENT}]", spinner="dots"):
        servers = get_all_servers()

    if not servers:
        console.print(f"[bold {FAIL}]No servers found[/bold {FAIL}] (check your API key / panel_url).")
        sys.exit(1)

    console.print(Align.center(f"[{TEXT}]{len(servers)}[/{TEXT}] server(s) found."))

    results = []
    start_time = time.time()
    for server in servers:
        attrs = server["attributes"]
        try:
            stats = backup_server(server, destination_root)
            results.append((attrs["name"], stats))
        except Exception as e:
            console.print(f"  [bold {FAIL}]Unexpected error[/bold {FAIL}] on this server, moving on: {e}")
            results.append((attrs["name"], {
                "files_ok": 0, "files_failed": 0, "excluded": 0, "db_ok": 0, "db_failed": 0
            }))

    elapsed = time.time() - start_time
    print_summary_table(results, elapsed, destination_root)


if __name__ == "__main__":
    main()
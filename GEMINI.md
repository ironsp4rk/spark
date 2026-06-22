# Project Context

* **Title:** S.P.A.R.K. (Standalone Package Acquisition & Resolution Kit). 
* **Command:** `spark`
* **Environment:** Linux (SteamOS / KDE Plasma), Immutable Root Filesystem
* **Language:** Python
* **Package Manager / Tooling:** `uv`
* **Linter & Formatter:** `ruff`
* **Static Analysis:** `mypy`
* **Test Framework:** `pytest`

## Project Objective
`spark` is a custom, standalone package manager designed to acquire, extract, and integrate pre-compiled application binaries from arbitrary web sources into user-space (`~/.local`). It bypasses system-level package managers to operate flawlessly on read-only root filesystems.

## Architectural Directives
* **Zero Root Access:** All operations must be strictly confined to the user's home directory. No `sudo`, no `/opt`, no `/usr/bin`.
* **Declarative Configs:** Application installations are defined entirely by local TOML recipes.
* **Idempotency:** Re-running an installation should safely overwrite/update existing files without leaving orphaned data.
* **Coding Standard:** Write robust, error-handled Python. Prioritize objective correctness and execution speed.
* **DRY Principle (Don't Repeat Yourself):** Avoid duplicated code, especially for repetitive dictionary lookups (e.g., parsing deeply nested TOML recipe fields) and standard error handling. Extract these into dedicated helper functions (e.g., `get_package_name(recipe)`, `fatal_error(msg)`).
* **Verification Workflow:** Always run the linter (`uv run ruff check`), formatter (`uv run ruff format`), static type checker (`uv run mypy .`), and tests (`uv run pytest`) after making any code changes.

## Directory Structure
* **Recipes:** `~/.config/spark/recipes/` (Contains the `.toml` files)
* **Installations:** `~/.local/opt/<package_name>/` (Extracted application files)
* **Executables:** `~/.local/bin/<cli_name>` (Symlinks pointing to the installation dir)
* **Desktop Integration:** `~/.local/share/applications/<package_name>.desktop`

## The Installation Pipeline
When executing a recipe, `spark` must perform the following sequence:

1.  **Version Resolution:** Parse the target URL using the defined strategy (e.g., regex) to extract the latest version string.
2.  **Acquisition:** Download the target archive (tarball, zip) into a temporary directory, injecting the resolved version into the URL template.
3.  **Verification (Optional):** If defined, download GPG signatures/public keys or checksums and verify the archive integrity before proceeding.
4.  **Extraction:** Clear any existing directory at `target_dir`. Extract the archive into `target_dir`, stripping top-level components if specified. Ensure primary binaries have `0o755` permissions.
5.  **Resolution (Linking):** Create a symlink from the `executable_path` to `~/.local/bin/<cli_name>`.
6.  **Integration:** Locate the `.desktop` file or generate a new one if not provided. Rewrite `Exec=` and `Icon=` paths to point to absolute paths within `target_dir`. Copy the file to `~/.local/share/applications/` and execute `update-desktop-database ~/.local/share/applications`.

## Example 1: CLI Symlink and Embedded .desktop File

```toml
[package]
name = "sublime-text"
description = "A sophisticated text editor for code, markup and prose."
cli_name = "subl"

[version]
strategy = "regex"
url = "[https://www.sublimetext.com/download](https://www.sublimetext.com/download)"
pattern = 'Build\s+(\d+)'

[download]
url = "[https://download.sublimetext.com/sublime_text_build](https://download.sublimetext.com/sublime_text_build)_{version}_x64.archive"
format = "zip"

[download.verify] # Optional block
type = "gpg"
signature_url = "[https://download.sublimetext.com/sublime_text_build](https://download.sublimetext.com/sublime_text_build)_{version}_x64.tar.xz.asc"
pubkey_url = "[https://download.sublimetext.com/sublimehq-pub.gpg](https://download.sublimetext.com/sublimehq-pub.gpg)"

[install]
target_dir = "~/.local/opt/sublime_text"
strip_components = 1
executable_path = "sublime_text"

[integration]
desktop_file = "sublime_text.desktop"
update_paths = true
```

## Example 2: No CLI and Generated .desktop File

```toml
[package]
name = "stellar-sync"
description = "Visual cloud synchronization tool."
# cli_name is intentionally omitted

[version]
strategy = "regex"
url = "https://example.com/stellar/releases"
pattern = 'release-(\d+\.\d+)'

[download]
url = "https://example.com/stellar/downloads/stellar-sync-v{version}-x86_64.tar.gz"
format = "tar.gz"

[install]
target_dir = "~/.local/opt/stellar-sync"
strip_components = 1
executable_path = "StellarSyncRun" # Target executable for the generated desktop file

[integration]
generate = true # Tells spark to build the file from scratch
icon = "assets/stellar-icon.png" # Relative path to the icon inside target_dir
categories = "Network;Utility;"
terminal = false
comment = "Sync your files visually"
```

## AI Tool Usage
* **Prefer Native Tools:** Always prioritize built-in native tools (`view_file`, `write_to_file`, `replace_file_content`, `grep_search`, `list_dir`) over executing generic shell commands (like `cat`, `grep`, `sed`, `ls`, or `awk`) via the terminal. Use the shell only when a specific native tool is not available for the task.

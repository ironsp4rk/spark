import argparse
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import urllib.parse
from typing import Any, Dict, NoReturn

CORE_REPO_URL = "https://github.com/ironsp4rk/spark-recipes"
LOCAL_RECIPES_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "config", "spark", "recipes")
)
CONFIG_HOME = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "spark"
)
GLOBAL_RECIPES_DIR = os.path.join(CONFIG_HOME, "recipes")
DEFAULT_CLI_DIR = "~/.local/bin"
DEFAULT_DESKTOP_DIR = "~/.local/share/applications"


def fatal_error(message: str) -> NoReturn:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def get_package_name(recipe: Dict[str, Any]) -> str:
    return recipe.get("package", {}).get("name", "app")


def get_relative_executable_path(recipe: Dict[str, Any]) -> str:
    return recipe.get("install", {}).get("executable_path", "")


def get_executable_path(recipe: Dict[str, Any], target_dir: str) -> str:
    return os.path.join(target_dir, get_relative_executable_path(recipe))


def get_cli_symlink_path(recipe: Dict[str, Any]) -> str | None:
    cli_name = recipe.get("package", {}).get("cli_name")
    if not cli_name:
        return None
    return os.path.join(get_cli_dir(recipe), cli_name)


def read_manifest(target_dir: str) -> Dict[str, Any] | None:
    manifest_path = os.path.join(target_dir, MANIFEST_FILENAME)
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "rb") as f:
                return tomllib.load(f)
        except Exception:
            pass
    return None


def build_http_request(url: str) -> urllib.request.Request:
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    return urllib.request.Request(url, headers=headers)


def get_desktop_dest_path(desktop_file: str) -> str:
    desktop_dir = os.path.expanduser(DEFAULT_DESKTOP_DIR)
    return os.path.join(desktop_dir, os.path.basename(desktop_file))


def remove_symlink_if_exists(path: str) -> None:
    if os.path.exists(path) or os.path.islink(path):
        os.remove(path)


def extract_pattern(text: str, pattern: str) -> str | None:
    if not pattern:
        return None
    match = re.search(pattern, text)
    return match.group(1) if match else None


def _get_spark_prefix() -> str:
    env_prefix = os.environ.get("SPARK_PREFIX")
    if env_prefix:
        return os.path.expanduser(env_prefix)

    default_prefix = "~/.local/opt"
    config_path = os.path.join(CONFIG_HOME, "config.toml")
    if os.path.exists(config_path):
        try:
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
                prefix = config.get("core", {}).get("prefix")
                if prefix:
                    return os.path.expanduser(prefix)
        except Exception as e:
            print(
                f"Warning: Failed to read config at {config_path}: {e}", file=sys.stderr
            )
    return os.path.expanduser(default_prefix)


SPARK_PREFIX = _get_spark_prefix()
MANIFEST_FILENAME = ".spark-manifest.toml"


def get_target_dir(recipe: Dict[str, Any]) -> str:
    dir_name = recipe.get("install", {}).get("dir_name")
    if not dir_name:
        dir_name = get_package_name(recipe)
    if not dir_name:
        fatal_error("Could not determine directory name for installation.")

    dir_name = dir_name.replace("/", "-").replace("\\", "-")
    return os.path.join(SPARK_PREFIX, dir_name)


def get_cli_dir(recipe: Dict[str, Any]) -> str:
    return os.path.expanduser(recipe.get("install", {}).get("cli_dir", DEFAULT_CLI_DIR))


def get_desktop_filename(recipe: Dict[str, Any]) -> str | None:
    integration = recipe.get("integration", {})
    desktop_file = integration.get("desktop_file")
    generate = integration.get("generate", False)

    if not desktop_file and not generate:
        return None

    if not desktop_file:
        pkg_name = get_package_name(recipe)
        desktop_file = f"{pkg_name}.desktop"

    return desktop_file


def update_desktop_database(desktop_dir: str):
    try:
        subprocess.run(["update-desktop-database", desktop_dir], check=True)
    except Exception as e:
        print(f"Warning: Failed to update desktop database: {e}", file=sys.stderr)


def load_recipe(recipe_path_or_name: str) -> Dict[str, Any]:
    resolved_path = os.path.expanduser(recipe_path_or_name)
    if os.path.exists(resolved_path):
        path = os.path.abspath(resolved_path)
    else:
        search_dirs = [
            os.path.abspath(LOCAL_RECIPES_DIR),
            os.path.expanduser(GLOBAL_RECIPES_DIR),
        ]

        path = ""
        for base_dir in search_dirs:
            if not os.path.exists(base_dir):
                continue

            # Check direct file
            direct_path = os.path.join(base_dir, f"{recipe_path_or_name}.toml")
            if os.path.exists(direct_path):
                path = direct_path
                break

            # Check immediate subdirectories (for repositories)
            for item in os.listdir(base_dir):
                sub_dir = os.path.join(base_dir, item)
                if os.path.isdir(sub_dir):
                    sub_path = os.path.join(sub_dir, f"{recipe_path_or_name}.toml")
                    if os.path.exists(sub_path):
                        path = sub_path
                        break
            if path:
                break

        if not path:
            # Fallback for error message
            path = os.path.expanduser(
                os.path.join(GLOBAL_RECIPES_DIR, f"{recipe_path_or_name}.toml")
            )

    if not os.path.exists(path):
        fatal_error(f"Recipe file not found for '{recipe_path_or_name}'")

    try:
        with open(path, "rb") as f:
            recipe = tomllib.load(f)
            recipe["_recipe_dir"] = os.path.dirname(path)
            print(f"Using recipe: {path}")
            return recipe
    except Exception as e:
        fatal_error(f"parsing recipe TOML file '{path}': {e}")


def get_local_pattern(recipe: Dict[str, Any]) -> str:
    version_config = recipe.get("version", {})
    return str(version_config.get("local_pattern") or version_config.get("pattern", ""))


def get_remote_pattern(recipe: Dict[str, Any]) -> str:
    version_config = recipe.get("version", {})
    return str(
        version_config.get("remote_pattern") or version_config.get("pattern", "")
    )


def get_local_version(recipe: Dict[str, Any]) -> str:
    cli_symlink_path = get_cli_symlink_path(recipe)

    executable_path = get_relative_executable_path(recipe)
    target_dir = get_target_dir(recipe)

    version_config = recipe.get("version", {})
    local_strategy = version_config.get("local_strategy", "manifest")

    if local_strategy == "manifest":
        if target_dir:
            manifest = read_manifest(target_dir)
            if manifest:
                return manifest.get("version", "")
        return ""

    if local_strategy == "file":
        local_file = version_config.get("local_file")
        if local_file and target_dir:
            local_file_path = os.path.join(target_dir, local_file)
            if os.path.exists(local_file_path):
                try:
                    with open(local_file_path, "r") as f:
                        content = f.read()
                    pattern = get_local_pattern(recipe)
                    version = extract_pattern(content, pattern)
                    if version:
                        return version
                except Exception:
                    pass
        return ""

    if local_strategy == "cli":
        check_paths = []
        if cli_symlink_path:
            check_paths.append(cli_symlink_path)
        if target_dir and executable_path:
            check_paths.append(os.path.join(target_dir, executable_path))

        for path in check_paths:
            if os.path.exists(path):
                try:
                    result = subprocess.run(
                        [path, "--version"], capture_output=True, text=True, check=True
                    )
                    pattern = get_local_pattern(recipe)
                    version = extract_pattern(result.stdout, pattern)
                    if version:
                        return version
                except Exception:
                    pass
    return ""


def get_remote_version(recipe: Dict[str, Any]) -> str:
    version_url = recipe.get("version", {}).get("url")
    pattern = get_remote_pattern(recipe)
    if not version_url or not pattern:
        fatal_error("version url or remote_pattern not defined in recipe.")

    search_linked_js = recipe.get("version", {}).get("search_linked_js", False)

    def fetch_url_text(url: str) -> str:
        print(f"Checking version URL: {url}")
        req = build_http_request(url)
        try:
            with urllib.request.urlopen(req) as response:
                encoding = response.headers.get_content_charset()
                if not isinstance(encoding, str):
                    encoding = "utf-8"
                content = response.read()
                if content.startswith(b"\x1f\x8b"):
                    import gzip

                    content = gzip.decompress(content)
                return content.decode(encoding, errors="replace")
        except Exception as e:
            fatal_error(f"fetching URL '{url}': {e}")

    content_to_search = fetch_url_text(version_url)

    if search_linked_js:
        # Extract all referenced script tags and preloaded JS files
        script_paths = re.findall(r'src="([^"]+\.js)"', content_to_search)
        preload_paths = re.findall(r'href="([^"]+\.js)"', content_to_search)
        all_js_paths = list(dict.fromkeys(script_paths + preload_paths))

        if not all_js_paths:
            fatal_error("Could not find any JS scripts referenced in the page HTML.")

        found_match = None
        for js_path in all_js_paths:
            js_url = urllib.parse.urljoin(version_url, js_path)
            js_text = fetch_url_text(js_url)
            found_match = extract_pattern(js_text, pattern)
            if found_match:
                break

        if not found_match:
            fatal_error("Could not parse remote version from any referenced scripts.")

        return found_match

    version = extract_pattern(content_to_search, pattern)
    if not version:
        fatal_error("Could not parse remote version from URL.")

    return version


def is_process_running(executable_name: str) -> bool:
    try:
        result = subprocess.run(["pgrep", "-x", executable_name], capture_output=True)
        return result.returncode == 0
    except Exception:
        return False


def ensure_not_running(executable_name: str):
    while is_process_running(executable_name):
        print(f"\nWarning: {executable_name} is currently running.", file=sys.stderr)
        print(
            f"Please save your work and close {executable_name} to proceed.",
            file=sys.stderr,
        )
        try:
            choice = (
                input("Select an option: [R]etry / [K]ill gracefully / [A]bort: ")
                .strip()
                .lower()
            )
        except KeyboardInterrupt:
            print("\nUpdate aborted.", file=sys.stderr)
            sys.exit(1)
        if choice == "a":
            print("Update aborted.", file=sys.stderr)
            sys.exit(1)
        elif choice == "k":
            print(f"Sending terminate signal to {executable_name}...", file=sys.stderr)
            try:
                subprocess.run(["pkill", "-x", executable_name])
            except Exception as e:
                print(f"Failed to send terminate signal: {e}", file=sys.stderr)
        elif choice == "r" or not choice:
            continue


def download_file(url: str, dest: str):
    req = build_http_request(url)
    try:
        with urllib.request.urlopen(req) as response, open(dest, "wb") as out_file:
            shutil.copyfileobj(response, out_file)
    except Exception as e:
        fatal_error(f"downloading {url}: {e}")


def verify_gpg(archive_path: str, signature_path: str, pubkey_path: str, tempdir: str):
    gnupg_home = os.path.join(tempdir, "gnupg_home")
    os.makedirs(gnupg_home, mode=0o700, exist_ok=True)

    import_cmd = ["gpg", "--homedir", gnupg_home, "--import", pubkey_path]
    result = subprocess.run(import_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Failed to import GPG public key:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    verify_cmd = [
        "gpg",
        "--homedir",
        gnupg_home,
        "--verify",
        signature_path,
        archive_path,
    ]
    result = subprocess.run(verify_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"GPG Signature verification FAILED:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    print("GPG Signature verification succeeded.")


def extract_archive(
    archive_path: str, format_type: str, target_dir: str, strip_components: int
):
    parent_dir = os.path.dirname(target_dir)
    os.makedirs(parent_dir, exist_ok=True)

    if os.path.exists(target_dir):
        shutil.rmtree(target_dir)

    with tempfile.TemporaryDirectory() as temp_extract_dir:
        if format_type.endswith(".zip"):
            import zipfile

            with zipfile.ZipFile(archive_path, "r") as zip_ref:
                zip_ref.extractall(temp_extract_dir)
        else:
            with tarfile.open(archive_path, "r:*") as tar:
                tar.extractall(path=temp_extract_dir)

        src_dir = temp_extract_dir
        if strip_components > 0:
            current = temp_extract_dir
            for _ in range(strip_components):
                contents = os.listdir(current)
                dirs = [d for d in contents if os.path.isdir(os.path.join(current, d))]
                if len(dirs) == 1:
                    current = os.path.join(current, dirs[0])
                else:
                    if len(contents) == 1:
                        current = os.path.join(current, contents[0])
                    break
            src_dir = current

        # Calculate package stats
        file_count = 0
        total_size = 0
        for root, _, files in os.walk(src_dir):
            for f in files:
                file_count += 1
                file_path = os.path.join(root, f)
                try:
                    total_size += os.path.getsize(file_path)
                except OSError:
                    pass

        size_mb = total_size / (1024 * 1024)
        print(f"Extracted {file_count} files ({size_mb:.2f} MB)")

        shutil.move(src_dir, target_dir)


def make_executable(recipe: Dict[str, Any], target_dir: str, dry_run: bool = False):
    executable_path = get_relative_executable_path(recipe)
    if executable_path:
        bin_path = os.path.join(target_dir, executable_path)
        if dry_run:
            print(f"[Dry-run] Would make executable: {bin_path}")
        else:
            if os.path.exists(bin_path):
                os.chmod(bin_path, 0o755)

    additional_executables = recipe.get("install", {}).get("additional_executables", [])
    for extra_exe in additional_executables:
        extra_path = os.path.join(target_dir, extra_exe)
        if dry_run:
            print(f"[Dry-run] Would make executable: {extra_path}")
        else:
            if os.path.exists(extra_path):
                os.chmod(extra_path, 0o755)


def create_cli_symlink(recipe: Dict[str, Any], target_dir: str, dry_run: bool = False):
    cli_symlink_path = get_cli_symlink_path(recipe)
    if not cli_symlink_path:
        return

    cli_dir = get_cli_dir(recipe)

    executable_path = get_relative_executable_path(recipe)
    target_exec = os.path.join(target_dir, executable_path)

    if dry_run:
        print(f"[Dry-run] Would create directory: {cli_dir}")
        print(f"[Dry-run] Would remove existing symlink: {cli_symlink_path}")
        print(f"[Dry-run] Would create symlink: {cli_symlink_path} -> {target_exec}")
    else:
        os.makedirs(cli_dir, exist_ok=True)
        remove_symlink_if_exists(cli_symlink_path)
        os.symlink(target_exec, cli_symlink_path)


def resolve_icon_path(recipe: Dict[str, Any], target_dir: str) -> tuple[str, str]:
    integration = recipe.get("integration", {})
    icon_rel_path = integration.get("icon")
    if not icon_rel_path:
        return "", ""

    expanded_icon = os.path.expanduser(icon_rel_path)
    if os.path.isabs(expanded_icon):
        return expanded_icon, ""

    # Check if it exists in target_dir
    target_icon = os.path.join(target_dir, icon_rel_path)
    if os.path.exists(target_icon):
        return target_icon, ""

    # Check recipe directory
    recipe_dir = recipe.get("_recipe_dir")
    if recipe_dir:
        recipe_icon = os.path.join(recipe_dir, icon_rel_path)
        if os.path.exists(recipe_icon):
            dest_icon = os.path.join(target_dir, os.path.basename(icon_rel_path))
            return dest_icon, recipe_icon
        else:
            print(
                f"Warning: Icon file '{icon_rel_path}' not found in target_dir or recipe directory.",
                file=sys.stderr,
            )
    else:
        print(
            f"Warning: Icon file '{icon_rel_path}' not found in target_dir.",
            file=sys.stderr,
        )
    return "", ""


def generate_new_desktop_file(
    recipe: Dict[str, Any], target_dir: str, icon_path: str
) -> str:
    integration = recipe.get("integration", {})
    desktop_config = integration.get("desktop", {})
    lines = ["[Desktop Entry]"]
    lines.append("Type=Application")

    # Override target executable path and icon path
    executable_path = get_relative_executable_path(recipe)
    new_exec = os.path.join(target_dir, executable_path)
    exec_args = integration.get("exec_args")
    if exec_args:
        new_exec = f"{new_exec} {exec_args}"

    # Write all key/value pairs from TOML [integration.desktop] directly
    for k, v in desktop_config.items():
        if k.lower() in ("exec", "icon", "type"):
            continue
        if isinstance(v, bool):
            v = "true" if v else "false"
        lines.append(f"{k}={v}")

    lines.append(f"Exec={new_exec}")
    if icon_path:
        lines.append(f"Icon={icon_path}")

    return "\n".join(lines) + "\n"


def patch_existing_desktop_file(
    recipe: Dict[str, Any],
    read_dir: str,
    planned_target_dir: str,
    icon_path: str,
    desktop_file: str,
) -> str:
    src_desktop = os.path.join(read_dir, desktop_file)
    if not os.path.exists(src_desktop):
        print(
            f"Warning: {desktop_file} not found in extracted files.",
            file=sys.stderr,
        )
        return ""

    with open(src_desktop, "r") as f:
        content = f.read()

    executable_path = get_relative_executable_path(recipe)
    new_exec = os.path.join(planned_target_dir, executable_path)

    escaped_exe = re.escape(os.path.basename(executable_path))
    content = re.sub(
        r"^Exec=(?:.*/)?" + escaped_exe + r"(.*)$",
        rf"Exec={new_exec}\1",
        content,
        flags=re.MULTILINE,
    )

    if icon_path:
        content = re.sub(
            r"^Icon=.*$",
            f"Icon={icon_path}",
            content,
            flags=re.MULTILINE,
        )

    return content


def install_desktop_file(
    recipe: Dict[str, Any], target_dir: str, dry_run: bool = False, active_dir: str = ""
):
    desktop_file = get_desktop_filename(recipe)
    if not desktop_file:
        return

    generate = recipe.get("integration", {}).get("generate", False)

    check_dir = active_dir if active_dir else target_dir
    icon_path, icon_source = resolve_icon_path(recipe, check_dir)

    if dry_run and icon_path and check_dir != target_dir:
        planned_icon = icon_path.replace(check_dir, target_dir, 1)
    else:
        planned_icon = icon_path

    if generate:
        content = generate_new_desktop_file(recipe, target_dir, planned_icon)
    else:
        content = patch_existing_desktop_file(
            recipe, check_dir, target_dir, planned_icon, desktop_file
        )
        if not content:
            return

    desktop_dir = os.path.expanduser(DEFAULT_DESKTOP_DIR)
    dest_path = get_desktop_dest_path(desktop_file)

    if dry_run:
        print(f"[Dry-run] Would create directory: {desktop_dir}")
        if icon_source:
            print(f"[Dry-run] Would copy icon from {icon_source} to {planned_icon}")
        print(f"[Dry-run] Would create/update desktop entry: {dest_path}")
        print("Planned Desktop Entry Content:")
        print("-----------------------------------------------------------")
        print(content.strip())
        print("-----------------------------------------------------------")
    else:
        if icon_source and planned_icon:
            shutil.copy2(icon_source, planned_icon)

        os.makedirs(desktop_dir, exist_ok=True)
        with open(dest_path, "w") as f:
            f.write(content)

        update_desktop_database(desktop_dir)


def update_repositories():
    local_base_dir = os.path.abspath(LOCAL_RECIPES_DIR)
    prod_base_dir = os.path.expanduser(GLOBAL_RECIPES_DIR)
    search_dirs = [local_base_dir, prod_base_dir]

    updated_any = False
    core_repo_found = False

    for base_dir in search_dirs:
        if not os.path.exists(base_dir):
            continue

        for item in os.listdir(base_dir):
            sub_dir = os.path.join(base_dir, item)
            if os.path.isdir(sub_dir):
                git_dir = os.path.join(sub_dir, ".git")
                if os.path.exists(git_dir) and os.path.isdir(git_dir):
                    if item == "core":
                        core_repo_found = True
                    print(f"Updating recipe repository: {sub_dir}")
                    try:
                        subprocess.run(["git", "-C", sub_dir, "pull"], check=True)
                        updated_any = True
                    except subprocess.CalledProcessError:
                        print(
                            f"Warning: Failed to update repository {sub_dir}",
                            file=sys.stderr,
                        )
                    except Exception as e:
                        print(
                            f"Warning: Error updating repository {sub_dir}: {e}",
                            file=sys.stderr,
                        )

    if not core_repo_found:
        core_dir = os.path.join(prod_base_dir, "core")
        print(f"Core repository not found. Cloning into {core_dir}...")
        os.makedirs(prod_base_dir, exist_ok=True)
        try:
            subprocess.run(["git", "clone", CORE_REPO_URL, core_dir], check=True)
            updated_any = True
        except subprocess.CalledProcessError:
            print(
                f"Warning: Failed to clone core repository to {core_dir}",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"Warning: Error cloning core repository: {e}", file=sys.stderr)

    if not updated_any:
        print("No recipe repositories found to update.")
    else:
        print("Finished updating recipe repositories.")


def process_install(
    recipe_arg: str, dry_run: bool, force: bool, is_upgrade: bool = False
):
    recipe = load_recipe(recipe_arg)
    name = get_package_name(recipe)

    if dry_run:
        print(f"Executing dry-run for {name}...")

    remote_version = get_remote_version(recipe)
    local_version = get_local_version(recipe)

    match_pattern = recipe.get("version", {}).get("match_pattern")
    if match_pattern:
        rm = re.search(match_pattern, remote_version)
        remote_cmp = rm.group(1) if rm else remote_version

        lm = re.search(match_pattern, local_version)
        local_cmp = lm.group(1) if lm else local_version
    else:
        remote_cmp = remote_version
        local_cmp = local_version

    print(f"Latest Version: {remote_version}")
    print(f"Local Version:  {local_version if local_version else 'Not installed'}")

    if is_upgrade:
        if not local_cmp:
            print(
                f"Error: {name} is not installed. Use 'spark install {recipe_arg}' to install."
            )
            sys.exit(1)

        if local_cmp == remote_cmp and not force:
            print(f"{name} is already up to date.\n")
            return

        if force and local_cmp == remote_cmp:
            print(f"Force upgrading {name} version {remote_version}...")
        else:
            print(f"Upgrading {name} to version {remote_version}...")
    else:
        if local_cmp and not force:
            print(
                f"Error: {name} is already installed (version {local_version}). Use 'spark upgrade {recipe_arg}' to update, or use --force to overwrite.\n"
            )
            sys.exit(1)

        if force and local_cmp:
            print(f"Force installing {name} version {remote_version}...")
        else:
            print(f"Installing {name} version {remote_version}...")

    executable_path = get_relative_executable_path(recipe)
    if executable_path:
        ensure_not_running(os.path.basename(executable_path))

    download_url_template = recipe.get("download", {}).get("url", "")
    if not download_url_template:
        fatal_error("download url not defined in recipe.")
    download_url = download_url_template.replace("{version}", remote_version)

    download_format = recipe.get("download", {}).get("format", "")
    if not download_format:
        if download_url.endswith(".zip"):
            download_format = "zip"
        else:
            download_format = "tar"

    verify_config = recipe.get("download", {}).get("verify", {})
    verify_type = verify_config.get("type")

    with tempfile.TemporaryDirectory() as tempdir:
        archive_filename = (
            f"package_{remote_version}.{download_format}"
            if download_format
            else "package"
        )
        archive_path = os.path.join(tempdir, archive_filename)

        print("Downloading package...")
        download_file(download_url, archive_path)

        if verify_type == "gpg":
            sig_url_template = verify_config.get("signature_url", "")
            pubkey_url = verify_config.get("pubkey_url", "")
            if sig_url_template and pubkey_url:
                sig_url = sig_url_template.replace("{version}", remote_version)
                sig_path = archive_path + ".asc"
                pubkey_path = os.path.join(tempdir, "pubkey.gpg")

                print("Downloading GPG signature and public key...")
                download_file(sig_url, sig_path)
                download_file(pubkey_url, pubkey_path)

                print("Verifying signature...")
                verify_gpg(archive_path, sig_path, pubkey_path, tempdir)

        target_dir = get_target_dir(recipe)

        strip_components = recipe.get("install", {}).get("strip_components", 0)

        if dry_run:
            active_dir = os.path.join(tempdir, "extracted_dry_run")
        else:
            active_dir = target_dir

        print("Extracting package...")
        if dry_run:
            print(f"[Dry-run] Would extract to: {target_dir}")
        extract_archive(
            archive_path,
            download_format or "",
            active_dir,
            strip_components,
        )

        make_executable(recipe, target_dir, dry_run=dry_run)

        create_cli_symlink(recipe, target_dir, dry_run=dry_run)

        print("Integrating desktop file...")
        install_desktop_file(recipe, target_dir, dry_run=dry_run, active_dir=active_dir)

        manifest_content = (
            f'package_name = "{name}"\n'
            f'recipe_name = "{recipe_arg}"\n'
            f'version = "{remote_version}"\n'
        )

        if dry_run:
            print("[Dry-run] Would create manifest with content:")
            print("-----------------------------------------------------------")
            print(manifest_content.strip())
            print("-----------------------------------------------------------")
            print("[Dry-run] Complete. No files were modified.\n")
            return
        else:
            os.makedirs(target_dir, exist_ok=True)
            manifest_path = os.path.join(target_dir, MANIFEST_FILENAME)
            with open(manifest_path, "w") as f:
                f.write(manifest_content)

    print(
        f"{name} has been successfully installed/updated to version {remote_version}!\n"
    )


def process_uninstall(recipe_arg: str, yes: bool, dry_run: bool = False):
    recipe = load_recipe(recipe_arg)
    name = get_package_name(recipe)

    if dry_run:
        print(f"Executing dry-run uninstallation for {name}...")

    local_version = get_local_version(recipe)
    target_dir = get_target_dir(recipe)

    if not os.path.exists(target_dir):
        print(f"{name} is not currently installed.")
        return

    print(
        f"{name} is installed (version {local_version if local_version else 'unknown'})."
    )

    if not yes:
        try:
            choice = (
                input(f"Are you sure you want to uninstall {name}? [y/N]: ")
                .strip()
                .lower()
            )
        except KeyboardInterrupt:
            print("\nUninstallation aborted.", file=sys.stderr)
            sys.exit(1)
        if choice != "y":
            print("Uninstallation aborted.")
            return

    executable_path = get_relative_executable_path(recipe)
    if executable_path:
        ensure_not_running(os.path.basename(executable_path))

    # Remove CLI symlink
    cli_symlink_path = get_cli_symlink_path(recipe)
    if cli_symlink_path and (
        os.path.exists(cli_symlink_path) or os.path.islink(cli_symlink_path)
    ):
        if dry_run:
            print(f"[Dry-run] Would remove CLI symlink: {cli_symlink_path}")
        else:
            print(f"Removing CLI symlink: {cli_symlink_path}")
            remove_symlink_if_exists(cli_symlink_path)

    # Remove desktop file
    desktop_file = get_desktop_filename(recipe)
    if desktop_file:
        desktop_dir = os.path.expanduser(DEFAULT_DESKTOP_DIR)
        dest_path = os.path.join(desktop_dir, os.path.basename(desktop_file))

        if os.path.exists(dest_path):
            if dry_run:
                print(f"[Dry-run] Would remove desktop file: {dest_path}")
                print(f"[Dry-run] Would update desktop database: {desktop_dir}")
            else:
                print(f"Removing desktop file: {dest_path}")
                os.remove(dest_path)
                update_desktop_database(desktop_dir)

    # Remove target dir
    if dry_run:
        print(f"[Dry-run] Would remove app directory: {target_dir}")
        print("[Dry-run] Complete. No files were modified.\n")
    else:
        print(f"Removing app directory: {target_dir}")
        shutil.rmtree(target_dir)
        print(f"Successfully uninstalled {name}.")


def process_upgrade(app: str | None, dry_run: bool):
    if app:
        process_install(app, dry_run, False, is_upgrade=True)
        return

    if not os.path.exists(SPARK_PREFIX):
        print("No packages installed via spark.")
        return

    manifests = []
    for item in os.listdir(SPARK_PREFIX):
        item_path = os.path.join(SPARK_PREFIX, item)
        if os.path.isdir(item_path):
            manifest = read_manifest(item_path)
            if manifest:
                manifests.append((item, manifest))
            else:
                manifest_file = os.path.join(item_path, MANIFEST_FILENAME)
                if os.path.exists(manifest_file):
                    print(
                        f"Warning: Could not read manifest at {manifest_file}",
                        file=sys.stderr,
                    )

    if not manifests:
        print("No packages with spark manifests found.")
        return

    for item, manifest in manifests:
        recipe_name = manifest.get("recipe_name")
        pkg_name = manifest.get("package_name", item)

        if not recipe_name:
            print(f"Skipping {pkg_name}: missing recipe_name in manifest.")
            continue

        print(f"--- Checking for updates for {pkg_name} ---")
        process_install(recipe_name, dry_run, False, is_upgrade=True)


def main():
    parser = argparse.ArgumentParser(
        description="S.P.A.R.K. (Standalone Package Acquisition & Resolution Kit) - A custom package manager designed to acquire, extract, and integrate pre-compiled application binaries from arbitrary web sources into user-space."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("update", help="Update all recipe repositories")

    install_parser = subparsers.add_parser("install", help="Install a package")
    install_parser.add_argument("recipe", help="Recipe name or path to TOML file")
    install_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force install a package (overwrite existing)",
    )
    install_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print installation plan without making changes",
    )

    upgrade_parser = subparsers.add_parser("upgrade", help="Upgrade installed packages")
    upgrade_parser.add_argument(
        "app", nargs="?", help="Specific app to upgrade (optional)"
    )
    upgrade_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print upgrade plan without making changes",
    )

    uninstall_parser = subparsers.add_parser("uninstall", help="Uninstall a package")
    uninstall_parser.add_argument("recipe", help="Recipe name or path to TOML file")
    uninstall_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Confirm uninstallation automatically",
    )
    uninstall_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print uninstallation plan without making changes",
    )

    args = parser.parse_args()

    if args.command == "update":
        update_repositories()
        sys.exit(0)

    if args.command == "install":
        process_install(
            args.recipe, getattr(args, "dry_run", False), getattr(args, "force", False)
        )
        sys.exit(0)

    if args.command == "upgrade":
        process_upgrade(args.app, getattr(args, "dry_run", False))
        sys.exit(0)

    if args.command == "uninstall":
        process_uninstall(
            args.recipe, getattr(args, "yes", False), getattr(args, "dry_run", False)
        )
        sys.exit(0)


if __name__ == "__main__":
    main()

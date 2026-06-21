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
from typing import Any, Dict


def load_recipe(recipe_path_or_name: str) -> Dict[str, Any]:
    resolved_path = os.path.expanduser(recipe_path_or_name)
    if os.path.exists(resolved_path):
        path = os.path.abspath(resolved_path)
    else:
        local_path = os.path.abspath(
            f"./config/spark/recipes/{recipe_path_or_name}.toml"
        )
        if os.path.exists(local_path):
            path = local_path
        else:
            path = os.path.expanduser(
                f"~/.config/spark/recipes/{recipe_path_or_name}.toml"
            )

    if not os.path.exists(path):
        print(f"Error: Recipe file not found at '{path}'", file=sys.stderr)
        sys.exit(1)

    try:
        with open(path, "rb") as f:
            recipe = tomllib.load(f)
            recipe["_recipe_dir"] = os.path.dirname(path)
            print(f"Using recipe: {path}")
            return recipe
    except Exception as e:
        print(f"Error parsing recipe TOML file '{path}': {e}", file=sys.stderr)
        sys.exit(1)


def get_local_pattern(recipe: Dict[str, Any]) -> str:
    version_config = recipe.get("version", {})
    return str(version_config.get("local_pattern") or version_config.get("pattern", ""))


def get_remote_pattern(recipe: Dict[str, Any]) -> str:
    version_config = recipe.get("version", {})
    return str(
        version_config.get("remote_pattern") or version_config.get("pattern", "")
    )


def get_local_version(recipe: Dict[str, Any]) -> str:
    cli_name = recipe.get("package", {}).get("cli_name")
    executable_path = recipe.get("install", {}).get("executable_path")
    target_dir = os.path.expanduser(recipe.get("install", {}).get("target_dir", ""))
    bin_dir = os.path.expanduser(
        recipe.get("install", {}).get("bin_dir", "~/.local/bin")
    )

    version_config = recipe.get("version", {})
    local_strategy = version_config.get("local_strategy", "cli")

    if local_strategy == "file":
        local_file = version_config.get("local_file")
        if local_file and target_dir:
            local_file_path = os.path.join(target_dir, local_file)
            if os.path.exists(local_file_path):
                try:
                    with open(local_file_path, "r") as f:
                        content = f.read()
                    pattern = get_local_pattern(recipe)
                    if pattern:
                        match = re.search(pattern, content)
                        if match:
                            return match.group(1)
                except Exception:
                    pass
        return ""

    if local_strategy == "cli":
        check_paths = []
        if cli_name:
            check_paths.append(os.path.join(bin_dir, cli_name))
        if target_dir and executable_path:
            check_paths.append(os.path.join(target_dir, executable_path))

        for path in check_paths:
            if os.path.exists(path):
                try:
                    result = subprocess.run(
                        [path, "--version"], capture_output=True, text=True, check=True
                    )
                    pattern = get_local_pattern(recipe)
                    if pattern:
                        match = re.search(pattern, result.stdout)
                        if match:
                            return match.group(1)
                except Exception:
                    pass
    return ""


def get_remote_version(recipe: Dict[str, Any]) -> str:
    version_url = recipe.get("version", {}).get("url")
    pattern = get_remote_pattern(recipe)
    if not version_url or not pattern:
        print(
            "Error: version url or remote_pattern not defined in recipe.",
            file=sys.stderr,
        )
        sys.exit(1)

    search_linked_js = recipe.get("version", {}).get("search_linked_js", False)
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

    def fetch_url_text(url: str) -> str:
        print(f"Checking version URL: {url}")
        req = urllib.request.Request(url, headers=headers)
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
            print(f"Error fetching URL '{url}': {e}", file=sys.stderr)
            sys.exit(1)

    content_to_search = fetch_url_text(version_url)

    if search_linked_js:
        # Extract all referenced script tags and preloaded JS files
        script_paths = re.findall(r'src="([^"]+\.js)"', content_to_search)
        preload_paths = re.findall(r'href="([^"]+\.js)"', content_to_search)
        all_js_paths = list(dict.fromkeys(script_paths + preload_paths))

        if not all_js_paths:
            print(
                "Error: Could not find any JS scripts referenced in the page HTML.",
                file=sys.stderr,
            )
            sys.exit(1)

        found_match = None
        for js_path in all_js_paths:
            js_url = urllib.parse.urljoin(version_url, js_path)
            js_text = fetch_url_text(js_url)
            match = re.search(pattern, js_text)
            if match:
                found_match = match.group(1)
                break

        if not found_match:
            print(
                "Error: Could not parse remote version from any referenced scripts.",
                file=sys.stderr,
            )
            sys.exit(1)

        return found_match

    match = re.search(pattern, content_to_search)
    if not match:
        print("Error: Could not parse remote version from URL.", file=sys.stderr)
        sys.exit(1)

    return match.group(1)


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
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as response, open(dest, "wb") as out_file:
            shutil.copyfileobj(response, out_file)
    except Exception as e:
        print(f"Error downloading {url}: {e}", file=sys.stderr)
        sys.exit(1)


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
    executable_path = recipe.get("install", {}).get("executable_path", "")
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


def create_symlink(recipe: Dict[str, Any], target_dir: str, dry_run: bool = False):
    cli_name = recipe.get("package", {}).get("cli_name")
    if not cli_name:
        return

    bin_dir = os.path.expanduser(
        recipe.get("install", {}).get("bin_dir", "~/.local/bin")
    )
    symlink_path = os.path.join(bin_dir, cli_name)

    executable_path = recipe.get("install", {}).get("executable_path", "")
    target_exec = os.path.join(target_dir, executable_path)

    if dry_run:
        print(f"[Dry-run] Would create directory: {bin_dir}")
        print(f"[Dry-run] Would remove existing symlink: {symlink_path}")
        print(f"[Dry-run] Would create symlink: {symlink_path} -> {target_exec}")
    else:
        os.makedirs(bin_dir, exist_ok=True)
        if os.path.exists(symlink_path) or os.path.islink(symlink_path):
            os.remove(symlink_path)
        os.symlink(target_exec, symlink_path)


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
    executable_path = recipe.get("install", {}).get("executable_path", "")
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
    recipe: Dict[str, Any], target_dir: str, icon_path: str, desktop_file: str
) -> str:
    src_desktop = os.path.join(target_dir, desktop_file)
    if not os.path.exists(src_desktop):
        print(
            f"Warning: {desktop_file} not found in extracted files.",
            file=sys.stderr,
        )
        return ""

    with open(src_desktop, "r") as f:
        content = f.read()

    executable_path = recipe.get("install", {}).get("executable_path", "")
    new_exec = os.path.join(target_dir, executable_path)

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
    integration = recipe.get("integration", {})
    desktop_file = integration.get("desktop_file")
    generate = integration.get("generate", False)

    if not desktop_file and not generate:
        return

    if not desktop_file:
        pkg_name = recipe.get("package", {}).get("name", "app")
        desktop_file = f"{pkg_name}.desktop"

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
            recipe, check_dir, planned_icon, desktop_file
        )
        if not content:
            return

    desktop_dir = os.path.expanduser("~/.local/share/applications")
    dest_path = os.path.join(desktop_dir, os.path.basename(desktop_file))

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

        try:
            subprocess.run(["update-desktop-database", desktop_dir], check=True)
        except Exception as e:
            print(f"Warning: Failed to update desktop database: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="S.P.A.R.K. (Standalone Package Acquisition & Resolution Kit) - A custom package manager designed to acquire, extract, and integrate pre-compiled application binaries from arbitrary web sources into user-space."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser(
        "install", help="Install or update a package recipe"
    )
    install_parser.add_argument("recipe", help="Recipe name or path to TOML file")
    install_parser.add_argument(
        "-f", "--force", action="store_true", help="Force update/re-install"
    )
    install_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print installation plan without making changes",
    )

    args = parser.parse_args()

    if args.command == "install":
        recipe = load_recipe(args.recipe)
        name = recipe.get("package", {}).get("name", "Unknown")
        dry_run = getattr(args, "dry_run", False)

        if dry_run:
            print(f"Executing dry-run for {name}...")
        else:
            print(f"Installing {name}...")

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

        if local_cmp == remote_cmp and not args.force:
            print(f"{name} is already installed and up to date.")
            sys.exit(0)

        if args.force and local_cmp == remote_cmp:
            print(f"Force updating/re-installing {name} version {remote_version}...")
        else:
            print(f"Updating/Installing {name} to version {remote_version}...")

        executable_path = recipe.get("install", {}).get("executable_path", "")
        if executable_path and not dry_run:
            ensure_not_running(os.path.basename(executable_path))

        download_url_template = recipe.get("download", {}).get("url", "")
        if not download_url_template:
            print("Error: download url not defined in recipe.", file=sys.stderr)
            sys.exit(1)
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

            target_dir = os.path.expanduser(
                recipe.get("install", {}).get("target_dir", "")
            )
            if not target_dir:
                print(
                    "Error: install target_dir not defined in recipe.",
                    file=sys.stderr,
                )
                sys.exit(1)

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

            create_symlink(recipe, target_dir, dry_run=dry_run)

            print("Integrating desktop file...")
            install_desktop_file(
                recipe, target_dir, dry_run=dry_run, active_dir=active_dir
            )

            if dry_run:
                print("[Dry-run] Complete. No files were modified.")
                sys.exit(0)

        print(
            f"{name} has been successfully installed/updated to version {remote_version}!"
        )


if __name__ == "__main__":
    main()

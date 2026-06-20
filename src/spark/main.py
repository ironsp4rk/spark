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
            print(f"Using recipe: {path}")
            return recipe
    except Exception as e:
        print(f"Error parsing recipe TOML file '{path}': {e}", file=sys.stderr)
        sys.exit(1)


def get_local_version(recipe: Dict[str, Any]) -> str:
    bin_name = recipe.get("package", {}).get("bin_name")
    executable_path = recipe.get("install", {}).get("executable_path")
    target_dir = os.path.expanduser(recipe.get("install", {}).get("target_dir", ""))
    bin_dir = os.path.expanduser(
        recipe.get("install", {}).get("bin_dir", "~/.local/bin")
    )

    check_paths = []
    if bin_name:
        check_paths.append(os.path.join(bin_dir, bin_name))
    if target_dir and executable_path:
        check_paths.append(os.path.join(target_dir, executable_path))

    for path in check_paths:
        if os.path.exists(path):
            try:
                result = subprocess.run(
                    [path, "--version"], capture_output=True, text=True, check=True
                )
                pattern = recipe.get("version", {}).get("local_pattern") or recipe.get(
                    "version", {}
                ).get("pattern", "")
                if pattern:
                    match = re.search(pattern, result.stdout)
                    if match:
                        return match.group(1)
            except Exception:
                pass
    return ""


def get_remote_version(recipe: Dict[str, Any]) -> str:
    version_url = recipe.get("version", {}).get("url")
    pattern = recipe.get("version", {}).get("pattern")
    if not version_url or not pattern:
        print("Error: version url or pattern not defined in recipe.", file=sys.stderr)
        sys.exit(1)

    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    req = urllib.request.Request(version_url, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            html = response.read().decode("utf-8")
    except Exception as e:
        print(f"Error fetching version URL: {e}", file=sys.stderr)
        sys.exit(1)

    match = re.search(pattern, html)
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
    bin_name = recipe.get("package", {}).get("bin_name")
    if not bin_name:
        return

    bin_dir = os.path.expanduser(
        recipe.get("install", {}).get("bin_dir", "~/.local/bin")
    )
    symlink_path = os.path.join(bin_dir, bin_name)

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


def update_desktop_entry(
    recipe: Dict[str, Any], target_dir: str, dry_run: bool = False, active_dir: str = ""
):
    integration = recipe.get("integration", {})
    desktop_file = integration.get("desktop_file")
    if not desktop_file:
        return

    check_dir = active_dir if active_dir else target_dir
    src_desktop = os.path.join(check_dir, desktop_file)
    if not os.path.exists(src_desktop):
        print(
            f"Warning: {desktop_file} not found in extracted files.",
            file=sys.stderr,
        )
        return

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

    icon_rel_path = integration.get("icon")
    if icon_rel_path:
        icon_path = os.path.join(target_dir, icon_rel_path)
        content = re.sub(
            r"^Icon=.*$",
            f"Icon={icon_path}",
            content,
            flags=re.MULTILINE,
        )

    desktop_dir = os.path.expanduser("~/.local/share/applications")
    dest_path = os.path.join(desktop_dir, os.path.basename(desktop_file))

    if dry_run:
        print(f"[Dry-run] Would create directory: {desktop_dir}")
        print(f"[Dry-run] Would create/update desktop entry: {dest_path}")
        print("Planned Desktop Entry Content:")
        print("-----------------------------------------------------------")
        print(content.strip())
        print("-----------------------------------------------------------")
    else:
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

        print(f"Latest Version: {remote_version}")
        print(f"Local Version:  {local_version if local_version else 'Not installed'}")

        if local_version == remote_version and not args.force:
            print(f"{name} is already installed and up to date.")
            sys.exit(0)

        if args.force and local_version == remote_version:
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
            update_desktop_entry(
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

import io
import os
import sys
import unittest
from unittest.mock import MagicMock, mock_open, patch

# Add src directory to path to import the spark package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
from spark import main


class TestSparkInstaller(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_get_remote_version(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.headers.get_content_charset.return_value = "utf-8"
        mock_response.read.return_value = b"<html><p>Version 2.0.4</p></html>"
        mock_urlopen.return_value.__enter__.return_value = mock_response

        recipe = {
            "version": {
                "url": "https://example.com",
                "pattern": r"Version\s+(\d+\.\d+\.\d+)",
            }
        }
        version = main.get_remote_version(recipe)
        self.assertEqual(version, "2.0.4")

    @patch("urllib.request.urlopen")
    def test_get_remote_version_with_search_linked_js(self, mock_urlopen):
        mock_response_html = MagicMock()
        mock_response_html.headers.get_content_charset.return_value = "utf-8"
        mock_response_html.read.return_value = (
            b'<html><script src="main-12345.js" type="module"></script></html>'
        )

        mock_response_js = MagicMock()
        mock_response_js.headers.get_content_charset.return_value = "utf-8"
        mock_response_js.read.return_value = (
            b'const downloadUrl = "https://example.com/stable/2.0.4/app.tar.gz";'
        )

        mock_urlopen.side_effect = [
            MagicMock(__enter__=MagicMock(return_value=mock_response_html)),
            MagicMock(__enter__=MagicMock(return_value=mock_response_js)),
        ]

        recipe = {
            "version": {
                "url": "https://example.com/download",
                "search_linked_js": True,
                "pattern": r"stable/(\d+\.\d+\.\d+)/app\.tar\.gz",
            }
        }
        version = main.get_remote_version(recipe)
        self.assertEqual(version, "2.0.4")

    @patch("urllib.request.urlopen")
    def test_get_remote_version_gzip(self, mock_urlopen):
        import gzip

        compressed = gzip.compress(b"<html><p>Version 3.2.1</p></html>")

        mock_response = MagicMock()
        mock_response.headers.get_content_charset.return_value = "utf-8"
        mock_response.read.return_value = compressed
        mock_urlopen.return_value.__enter__.return_value = mock_response

        recipe = {
            "version": {
                "url": "https://example.com",
                "pattern": r"Version\s+(\d+\.\d+\.\d+)",
            }
        }
        version = main.get_remote_version(recipe)
        self.assertEqual(version, "3.2.1")

    @patch("os.path.exists")
    @patch("subprocess.run")
    def test_get_local_version_installed(self, mock_run, mock_exists):
        mock_exists.return_value = True

        mock_proc = MagicMock()
        mock_proc.stdout = "Fake App Version 0.9.0\n"
        mock_run.return_value = mock_proc

        recipe = {
            "package": {"cli_name": "fakebin"},
            "install": {
                "target_dir": "~/.local/opt/fake_app",
                "executable_path": "fake_app",
            },
            "version": {"pattern": r"Version\s+(\d+\.\d+\.\d+)"},
        }

        version = main.get_local_version(recipe)
        self.assertEqual(version, "0.9.0")

    @patch("os.path.exists")
    @patch("subprocess.run")
    def test_get_local_version_with_local_pattern(self, mock_run, mock_exists):
        mock_exists.return_value = True

        mock_proc = MagicMock()
        mock_proc.stdout = "v1.2.3-release\n"
        mock_run.return_value = mock_proc

        recipe = {
            "package": {"cli_name": "fakebin"},
            "install": {
                "target_dir": "~/.local/opt/fake_app",
                "executable_path": "fake_app",
            },
            "version": {
                "pattern": r"Version\s+(\d+\.\d+\.\d+)",
                "local_pattern": r"v(\d+\.\d+\.\d+)",
            },
        }

        version = main.get_local_version(recipe)
        self.assertEqual(version, "1.2.3")

    @patch("os.path.exists")
    def test_get_local_version_not_installed(self, mock_exists):
        mock_exists.return_value = False
        recipe = {
            "package": {"cli_name": "fakebin"},
            "install": {
                "target_dir": "~/.local/opt/fake_app",
                "executable_path": "fake_app",
            },
            "version": {"pattern": r"Version\s+(\d+\.\d+\.\d+)"},
        }
        version = main.get_local_version(recipe)
        self.assertEqual(version, "")

    @patch("subprocess.run")
    def test_verify_gpg_success(self, mock_run):
        mock_run.return_value.returncode = 0
        try:
            main.verify_gpg("tarball", "sig", "pubkey", "/tmp")
        except SystemExit:
            self.fail("verify_gpg raised SystemExit unexpectedly")

    @patch("subprocess.run")
    def test_verify_gpg_fail(self, mock_run):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "gpg: BAD signature"

        with self.assertRaises(SystemExit):
            main.verify_gpg("tarball", "sig", "pubkey", "/tmp")

    @patch("os.makedirs")
    @patch("os.path.expanduser")
    @patch("os.path.exists")
    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data=(
            "[Desktop Entry]\n"
            "Version=1.0\n"
            "Type=Application\n"
            "Name=Fake App\n"
            "Exec=/opt/fake_app/fake_app %F\n"
            "Icon=fake-app\n"
        ),
    )
    @patch("subprocess.run")
    def test_install_desktop_file(
        self, mock_run, mock_file, mock_exists, mock_expanduser, mock_makedirs
    ):
        mock_expanduser.side_effect = lambda path: path.replace("~", "/home/user")
        mock_exists.side_effect = lambda path: True

        recipe = {
            "install": {"executable_path": "fake_app"},
            "integration": {
                "desktop_file": "fake-app.desktop",
                "icon": "Icon/256x256/fake-app.png",
            },
        }

        main.install_desktop_file(recipe, "/home/user/.local/opt/fake_app")

        mock_file.assert_any_call(
            "/home/user/.local/opt/fake_app/fake-app.desktop", "r"
        )
        mock_file.assert_any_call(
            "/home/user/.local/share/applications/fake-app.desktop", "w"
        )

        write_calls = mock_file().write.call_args_list
        written_content = "".join(call[0][0] for call in write_calls)
        self.assertIn(
            "Exec=/home/user/.local/opt/fake_app/fake_app %F",
            written_content,
        )
        self.assertIn(
            "Icon=/home/user/.local/opt/fake_app/Icon/256x256/fake-app.png",
            written_content,
        )

    @patch("spark.main.get_remote_version")
    @patch("spark.main.get_local_version")
    @patch("spark.main.download_file")
    @patch("spark.main.verify_gpg")
    @patch("spark.main.load_recipe")
    def test_main_no_force_already_up_to_date(
        self,
        mock_load_recipe,
        mock_verify,
        mock_download,
        mock_local_version,
        mock_remote_version,
    ):
        mock_remote_version.return_value = "1.0.0"
        mock_local_version.return_value = "1.0.0"
        mock_load_recipe.return_value = {
            "package": {"name": "fake-app"},
            "version": {"url": "url", "pattern": "pat"},
            "install": {"target_dir": "dir"},
        }

        with patch("sys.argv", ["spark", "install", "fake-app"]):
            with self.assertRaises(SystemExit) as cm:
                main.main()
            self.assertEqual(cm.exception.code, 0)

        mock_download.assert_not_called()

    @patch("spark.main.get_remote_version")
    @patch("spark.main.get_local_version")
    @patch("spark.main.download_file")
    @patch("spark.main.verify_gpg")
    @patch("spark.main.extract_archive")
    @patch("spark.main.make_executable")
    @patch("spark.main.create_symlink")
    @patch("spark.main.install_desktop_file")
    @patch("spark.main.ensure_not_running")
    @patch("spark.main.load_recipe")
    def test_main_force_already_up_to_date(
        self,
        mock_load_recipe,
        mock_ensure_not_running,
        mock_update_desktop,
        mock_create_symlink,
        mock_make_executable,
        mock_extract_archive,
        mock_verify,
        mock_download,
        mock_local_version,
        mock_remote_version,
    ):
        mock_remote_version.return_value = "1.0.0"
        mock_local_version.return_value = "1.0.0"
        mock_load_recipe.return_value = {
            "package": {"name": "fake-app"},
            "version": {"url": "url", "pattern": "pat"},
            "download": {
                "url": "http://example.com/{version}.tar.xz",
                "format": "tar.xz",
            },
            "install": {"target_dir": "dir", "executable_path": "fakebin"},
        }

        with patch("sys.argv", ["spark", "install", "fake-app", "--force"]):
            main.main()

        self.assertTrue(mock_download.called)
        mock_ensure_not_running.assert_called_once()
        mock_extract_archive.assert_called_once()
        mock_make_executable.assert_called_once()
        mock_create_symlink.assert_called_once()
        mock_update_desktop.assert_called_once()

    @patch("subprocess.run")
    def test_is_process_running_true(self, mock_run):
        mock_run.return_value.returncode = 0
        self.assertTrue(main.is_process_running("fake_app"))

    @patch("subprocess.run")
    def test_is_process_running_false(self, mock_run):
        mock_run.return_value.returncode = 1
        self.assertFalse(main.is_process_running("fake_app"))

    @patch("spark.main.is_process_running")
    def test_ensure_not_running_inactive(self, mock_is_running):
        mock_is_running.return_value = False
        main.ensure_not_running("fake_app")

    @patch("spark.main.is_process_running")
    @patch("builtins.input")
    def test_ensure_not_running_abort(self, mock_input, mock_is_running):
        mock_is_running.return_value = True
        mock_input.return_value = "a"
        with self.assertRaises(SystemExit) as cm:
            main.ensure_not_running("fake_app")
        self.assertEqual(cm.exception.code, 1)

    @patch("spark.main.is_process_running")
    @patch("builtins.input")
    def test_ensure_not_running_retry(self, mock_input, mock_is_running):
        mock_is_running.side_effect = [True, False]
        mock_input.return_value = "r"
        main.ensure_not_running("fake_app")
        self.assertEqual(mock_input.call_count, 1)

    @patch("spark.main.is_process_running")
    @patch("builtins.input")
    @patch("subprocess.run")
    def test_ensure_not_running_kill(self, mock_run, mock_input, mock_is_running):
        mock_is_running.side_effect = [True, False]
        mock_input.return_value = "k"
        main.ensure_not_running("fake_app")
        mock_run.assert_called_once_with(["pkill", "-x", "fake_app"])

    @patch("os.path.exists")
    @patch("os.chmod")
    def test_make_executable(self, mock_chmod, mock_exists):
        mock_exists.return_value = True
        recipe = {
            "install": {
                "executable_path": "fake_app",
                "additional_executables": ["helper_tool"],
            }
        }
        main.make_executable(recipe, "/opt/fake")
        mock_chmod.assert_any_call("/opt/fake/fake_app", 0o755)
        mock_chmod.assert_any_call("/opt/fake/helper_tool", 0o755)

    @patch("os.makedirs")
    @patch("os.path.exists")
    @patch("os.remove")
    @patch("os.symlink")
    def test_create_symlink_custom_bin_dir(
        self, mock_symlink, mock_remove, mock_exists, mock_makedirs
    ):
        mock_exists.return_value = True
        recipe = {
            "package": {"cli_name": "fakebin_custom"},
            "install": {
                "executable_path": "fake_app",
                "bin_dir": "/custom/bin",
            },
        }
        main.create_symlink(recipe, "/opt/fake")
        mock_makedirs.assert_called_once_with("/custom/bin", exist_ok=True)
        mock_remove.assert_called_once_with("/custom/bin/fakebin_custom")
        mock_symlink.assert_called_once_with(
            "/opt/fake/fake_app", "/custom/bin/fakebin_custom"
        )

    @patch("os.path.expanduser")
    @patch("os.path.exists")
    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data=b"[package]\nname = 'direct'\n",
    )
    def test_load_recipe_direct_path(self, mock_file, mock_exists, mock_expanduser):
        mock_expanduser.side_effect = lambda path: path.replace("~", "/home/user")
        mock_exists.side_effect = lambda path: path == "/home/user/recipe.toml"
        recipe = main.load_recipe("~/recipe.toml")
        self.assertEqual(recipe.get("package", {}).get("name"), "direct")
        mock_file.assert_called_once_with(
            os.path.abspath("/home/user/recipe.toml"), "rb"
        )

    @patch("os.path.expanduser")
    @patch("os.path.exists")
    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data=b"[package]\nname = 'direct'\n",
    )
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_load_recipe_prints_path(
        self, mock_stdout, mock_file, mock_exists, mock_expanduser
    ):
        mock_expanduser.side_effect = lambda path: path.replace("~", "/home/user")
        mock_exists.side_effect = lambda path: path == "/home/user/recipe.toml"
        main.load_recipe("~/recipe.toml")
        expected_path = os.path.abspath("/home/user/recipe.toml")
        self.assertIn(f"Using recipe: {expected_path}", mock_stdout.getvalue())

    @patch("os.path.expanduser")
    @patch("os.path.exists")
    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data=b"[package]\nname = 'local'\n",
    )
    def test_load_recipe_prioritize_local(
        self, mock_file, mock_exists, mock_expanduser
    ):
        mock_expanduser.side_effect = lambda path: path.replace("~", "/home/user")
        expected_local_path = os.path.abspath("./config/spark/recipes/myrecipe.toml")
        mock_exists.side_effect = lambda path: path == expected_local_path

        recipe = main.load_recipe("myrecipe")
        self.assertEqual(recipe.get("package", {}).get("name"), "local")
        mock_file.assert_called_once_with(expected_local_path, "rb")

    @patch("os.path.expanduser")
    @patch("os.path.exists")
    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data=b"[package]\nname = 'global'\n",
    )
    def test_load_recipe_fallback_global(self, mock_file, mock_exists, mock_expanduser):
        mock_expanduser.side_effect = lambda path: path.replace("~", "/home/user")
        expected_global_path = "/home/user/.config/spark/recipes/myrecipe.toml"
        mock_exists.side_effect = lambda path: path == expected_global_path

        recipe = main.load_recipe("myrecipe")
        self.assertEqual(recipe.get("package", {}).get("name"), "global")
        mock_file.assert_called_once_with(expected_global_path, "rb")

    @patch("os.path.expanduser")
    @patch("os.path.exists")
    def test_load_recipe_not_found(self, mock_exists, mock_expanduser):
        mock_expanduser.side_effect = lambda path: path.replace("~", "/home/user")
        mock_exists.return_value = False
        with self.assertRaises(SystemExit):
            main.load_recipe("myrecipe")

    @patch("os.makedirs")
    @patch("os.path.expanduser")
    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    @patch("subprocess.run")
    def test_install_desktop_file_generate(
        self, mock_run, mock_file, mock_exists, mock_expanduser, mock_makedirs
    ):
        mock_expanduser.side_effect = lambda path: path.replace("~", "/home/user")
        mock_exists.return_value = False

        recipe = {
            "package": {
                "name": "generated-app",
                "description": "A generated application",
            },
            "install": {
                "executable_path": "gen_app",
            },
            "integration": {
                "generate": True,
                "icon": "/absolute/path/to/icon.png",
                "desktop": {
                    "Name": "Generated App",
                    "Categories": "Utility;",
                    "Terminal": True,
                    "MimeType": "x-scheme-handler/gen-app;",
                    "StartupNotify": True,
                    "X-Custom-Key": "CustomValue",
                },
            },
        }

        main.install_desktop_file(recipe, "/home/user/.local/opt/gen_app")

        mock_file.assert_called_once_with(
            "/home/user/.local/share/applications/generated-app.desktop", "w"
        )

        write_calls = mock_file().write.call_args_list
        written_content = "".join(call[0][0] for call in write_calls)
        self.assertIn("[Desktop Entry]", written_content)
        self.assertIn("Type=Application", written_content)
        self.assertIn("Name=Generated App", written_content)
        self.assertIn("Exec=/home/user/.local/opt/gen_app/gen_app", written_content)
        self.assertIn("Icon=/absolute/path/to/icon.png", written_content)
        self.assertIn("Terminal=true", written_content)
        self.assertIn("Categories=Utility;", written_content)
        self.assertIn("MimeType=x-scheme-handler/gen-app;", written_content)
        self.assertIn("StartupNotify=true", written_content)
        self.assertIn("X-Custom-Key=CustomValue", written_content)

    @patch("os.makedirs")
    @patch("os.path.expanduser")
    @patch("os.path.exists")
    @patch("shutil.copy2")
    @patch("builtins.open", new_callable=mock_open)
    @patch("subprocess.run")
    def test_install_desktop_file_copy_icon_from_recipe(
        self,
        mock_run,
        mock_file,
        mock_copy,
        mock_exists,
        mock_expanduser,
        mock_makedirs,
    ):
        mock_expanduser.side_effect = lambda path: path.replace("~", "/home/user")
        mock_exists.side_effect = lambda path: path == "/home/user/recipes/icon.png"

        recipe = {
            "package": {"name": "App"},
            "install": {"executable_path": "app"},
            "_recipe_dir": "/home/user/recipes",
            "integration": {
                "generate": True,
                "icon": "icon.png",
                "desktop": {"Name": "App"},
            },
        }

        main.install_desktop_file(recipe, "/home/user/.local/opt/app")

        mock_copy.assert_called_once_with(
            "/home/user/recipes/icon.png", "/home/user/.local/opt/app/icon.png"
        )

    @patch("spark.main.get_remote_version")
    @patch("spark.main.get_local_version")
    @patch("spark.main.download_file")
    @patch("spark.main.extract_archive")
    @patch("spark.main.load_recipe")
    @patch("builtins.open", new_callable=mock_open)
    def test_main_dry_run(
        self,
        mock_file,
        mock_load_recipe,
        mock_extract,
        mock_download,
        mock_local_version,
        mock_remote_version,
    ):
        mock_remote_version.return_value = "2.0.4"
        mock_local_version.return_value = ""
        mock_load_recipe.return_value = {
            "package": {"name": "dry-app", "cli_name": "drybin"},
            "version": {"url": "url", "pattern": "pat"},
            "download": {
                "url": "http://example.com/{version}.tar.gz",
                "format": "tar.gz",
            },
            "install": {
                "target_dir": "/opt/dry",
                "executable_path": "dry_exe",
            },
            "integration": {
                "desktop_file": "dry.desktop",
            },
        }

        with patch("sys.argv", ["spark", "install", "dry-app", "--dry-run"]):
            with self.assertRaises(SystemExit) as cm:
                main.main()
            self.assertEqual(cm.exception.code, 0)

        self.assertTrue(mock_download.called)
        self.assertTrue(mock_extract.called)

    def test_extract_archive_strip_components(self):
        import tarfile
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "test.tar.gz")
            extract_dir = os.path.join(temp_dir, "extracted")

            os.makedirs(
                os.path.join(temp_dir, "source", "wrapper_dir", "actual_app", "bin")
            )
            with open(
                os.path.join(
                    temp_dir, "source", "wrapper_dir", "actual_app", "bin", "executable"
                ),
                "w",
            ) as f:
                f.write("test")

            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(
                    os.path.join(temp_dir, "source", "wrapper_dir"),
                    arcname="wrapper_dir",
                )

            main.extract_archive(
                archive_path, "tar.gz", extract_dir, strip_components=2
            )

            self.assertTrue(
                os.path.exists(os.path.join(extract_dir, "bin", "executable"))
            )

    @patch("urllib.request.urlopen")
    @patch("shutil.copyfileobj")
    @patch("builtins.open", new_callable=mock_open)
    def test_download_file_success(self, mock_file, mock_copy, mock_urlopen):
        mock_response = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_response
        main.download_file("http://example.com/file", "/dest/file")
        mock_urlopen.assert_called_once()
        mock_copy.assert_called_once_with(mock_response, mock_file())

    @patch("urllib.request.urlopen")
    def test_download_file_error(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Network error")
        with self.assertRaises(SystemExit) as cm:
            main.download_file("http://example.com/file", "/dest/file")
        self.assertEqual(cm.exception.code, 1)

    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data="app-version=4.5.6\n")
    def test_get_local_version_file_strategy(self, mock_file, mock_exists):
        mock_exists.return_value = True
        recipe = {
            "install": {"target_dir": "/opt/app"},
            "version": {
                "local_strategy": "file",
                "local_file": "version.txt",
                "pattern": r"app-version=([\d\.]+)",
            },
        }
        version = main.get_local_version(recipe)
        self.assertEqual(version, "4.5.6")
        mock_file.assert_called_once_with("/opt/app/version.txt", "r")

    @patch("os.path.exists")
    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data=(
            "[Desktop Entry]\n"
            "Name=Test App\n"
            "Exec=/old/path/app --arg\n"
            "Icon=/old/icon.png\n"
            "Terminal=false\n"
        ),
    )
    def test_patch_existing_desktop_file(self, mock_file, mock_exists):
        mock_exists.return_value = True
        recipe = {"install": {"executable_path": "bin/app"}}
        content = main.patch_existing_desktop_file(
            recipe, "/opt/new", "/new/icon.png", "app.desktop"
        )

        self.assertIn("Exec=/opt/new/bin/app --arg", content)
        self.assertIn("Icon=/new/icon.png", content)
        self.assertIn("Name=Test App", content)
        self.assertNotIn("/old/path/app", content)
        self.assertNotIn("/old/icon.png", content)

    @patch("os.path.exists")
    @patch("os.path.expanduser")
    def test_resolve_icon_path(self, mock_expanduser, mock_exists):
        mock_expanduser.side_effect = lambda x: x

        recipe = {"integration": {"icon": "/absolute/icon.png"}}
        path, src = main.resolve_icon_path(recipe, "/opt/app")
        self.assertEqual(path, "/absolute/icon.png")
        self.assertEqual(src, "")

        recipe = {"integration": {"icon": "icon.png"}}
        mock_exists.side_effect = lambda p: p == "/opt/app/icon.png"
        path, src = main.resolve_icon_path(recipe, "/opt/app")
        self.assertEqual(path, "/opt/app/icon.png")
        self.assertEqual(src, "")

        recipe = {"integration": {"icon": "icon.png"}, "_recipe_dir": "/recipes"}
        mock_exists.side_effect = lambda p: p == "/recipes/icon.png"
        path, src = main.resolve_icon_path(recipe, "/opt/app")
        self.assertEqual(path, "/opt/app/icon.png")
        self.assertEqual(src, "/recipes/icon.png")

    def test_get_remote_version_missing_url(self):
        recipe = {"version": {"pattern": ".*"}}
        with self.assertRaises(SystemExit) as cm:
            main.get_remote_version(recipe)
        self.assertEqual(cm.exception.code, 1)

    def test_get_remote_version_missing_pattern(self):
        recipe = {"version": {"url": "http://example.com"}}
        with self.assertRaises(SystemExit) as cm:
            main.get_remote_version(recipe)
        self.assertEqual(cm.exception.code, 1)

    @patch("urllib.request.urlopen")
    def test_get_remote_version_http_error(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("HTTP 404")
        recipe = {"version": {"url": "http://example.com", "pattern": ".*"}}
        with self.assertRaises(SystemExit) as cm:
            main.get_remote_version(recipe)
        self.assertEqual(cm.exception.code, 1)

    @patch("urllib.request.urlopen")
    def test_get_remote_version_no_match(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.headers.get_content_charset.return_value = "utf-8"
        mock_response.read.return_value = b"<html><p>No version here</p></html>"
        mock_urlopen.return_value.__enter__.return_value = mock_response

        recipe = {"version": {"url": "http://example.com", "pattern": r"Version (\d+)"}}
        with self.assertRaises(SystemExit) as cm:
            main.get_remote_version(recipe)
        self.assertEqual(cm.exception.code, 1)

    @patch("urllib.request.urlopen")
    def test_get_remote_version_js_not_found(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.headers.get_content_charset.return_value = "utf-8"
        mock_response.read.return_value = b"<html><body>No scripts</body></html>"
        mock_urlopen.return_value.__enter__.return_value = mock_response

        recipe = {
            "version": {
                "url": "http://example.com",
                "search_linked_js": True,
                "pattern": ".*",
            }
        }
        with self.assertRaises(SystemExit) as cm:
            main.get_remote_version(recipe)
        self.assertEqual(cm.exception.code, 1)

    @patch("spark.main.get_remote_version")
    @patch("spark.main.get_local_version")
    @patch("spark.main.download_file")
    @patch("spark.main.extract_archive")
    @patch("spark.main.make_executable")
    @patch("spark.main.create_symlink")
    @patch("spark.main.install_desktop_file")
    @patch("spark.main.ensure_not_running")
    @patch("spark.main.load_recipe")
    def test_main_match_pattern_comparison(
        self,
        mock_load_recipe,
        mock_ensure_not_running,
        mock_install_desktop,
        mock_create_symlink,
        mock_make_executable,
        mock_extract,
        mock_download,
        mock_local_version,
        mock_remote_version,
    ):
        mock_remote_version.return_value = "Release 2.5"
        mock_local_version.return_value = "v2.5"

        mock_load_recipe.return_value = {
            "package": {"name": "app"},
            "version": {"url": "url", "pattern": "pat", "match_pattern": r"(\d+\.\d+)"},
            "install": {"target_dir": "dir"},
        }

        with patch("sys.argv", ["spark", "install", "app"]):
            with self.assertRaises(SystemExit) as cm:
                main.main()
            self.assertEqual(cm.exception.code, 0)

        mock_download.assert_not_called()


if __name__ == "__main__":
    unittest.main()

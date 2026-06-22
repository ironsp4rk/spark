import io
import os
import sys
import unittest
from unittest.mock import MagicMock, mock_open, patch

# Add src directory to path to import the spark package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
from spark import main


class TestConfig(unittest.TestCase):
    def test_get_spark_prefix_default(self):
        with patch.dict(os.environ, clear=True):
            with patch("os.path.exists", return_value=False):
                root = main._get_spark_prefix()
                self.assertEqual(root, os.path.expanduser("~/.local/opt"))

    def test_get_spark_prefix_env_override(self):
        with patch.dict(os.environ, {"SPARK_PREFIX": "~/my/custom/prefix"}):
            root = main._get_spark_prefix()
            self.assertEqual(root, os.path.expanduser("~/my/custom/prefix"))

    def test_get_spark_prefix_from_config(self):
        config_content = b'[core]\nprefix = "~/custom/opt"'
        # Patch CONFIG_HOME since it's evaluated at module load
        with patch("spark.main.CONFIG_HOME", "/path/to/spark_config_home"):
            with patch("os.path.exists", return_value=True):
                with patch("builtins.open", mock_open(read_data=config_content)):
                    root = main._get_spark_prefix()
                    self.assertEqual(root, os.path.expanduser("~/custom/opt"))

    def test_get_spark_prefix_invalid_toml(self):
        config_content = b"invalid toml"
        with patch("spark.main.CONFIG_HOME", "/path/to/spark_config_home"):
            with patch("os.path.exists", return_value=True):
                with patch("builtins.open", mock_open(read_data=config_content)):
                    root = main._get_spark_prefix()
                    self.assertEqual(root, os.path.expanduser("~/.local/opt"))


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
            "package": {"cli_name": "fakebin", "name": "fake_app"},
            "install": {
                "dir_name": "fake_app",
                "executable_path": "fake_app",
            },
            "version": {
                "pattern": r"Version\s+(\d+\.\d+\.\d+)",
                "local_strategy": "cli",
            },
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
            "package": {"cli_name": "fakebin", "name": "fake_app"},
            "install": {
                "dir_name": "fake_app",
                "executable_path": "fake_app",
            },
            "version": {
                "pattern": r"Version\s+(\d+\.\d+\.\d+)",
                "local_pattern": r"v(\d+\.\d+\.\d+)",
                "local_strategy": "cli",
            },
        }

        version = main.get_local_version(recipe)
        self.assertEqual(version, "1.2.3")

    @patch("os.path.exists")
    def test_get_local_version_not_installed(self, mock_exists):
        mock_exists.return_value = False
        recipe = {
            "package": {"cli_name": "fakebin", "name": "fake_app"},
            "install": {
                "dir_name": "fake_app",
                "executable_path": "fake_app",
            },
            "version": {
                "pattern": r"Version\s+(\d+\.\d+\.\d+)",
                "local_strategy": "cli",
            },
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

        with patch("sys.argv", ["spark", "upgrade", "fake-app"]):
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
    @patch("spark.main.create_cli_symlink")
    @patch("spark.main.install_desktop_file")
    @patch("spark.main.ensure_not_running")
    @patch("spark.main.load_recipe")
    @patch("os.makedirs")
    @patch("builtins.open", new_callable=mock_open)
    def test_main_force_already_up_to_date(
        self,
        mock_file_open,
        mock_makedirs,
        mock_load_recipe,
        mock_ensure_not_running,
        mock_update_desktop,
        mock_create_cli_symlink,
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
            with self.assertRaises(SystemExit) as cm:
                main.main()
            self.assertEqual(cm.exception.code, 0)

        self.assertTrue(mock_download.called)
        mock_ensure_not_running.assert_called_once()
        mock_extract_archive.assert_called_once()
        mock_make_executable.assert_called_once()
        mock_create_cli_symlink.assert_called_once()
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
    def test_create_cli_symlink_custom_cli_dir(
        self, mock_symlink, mock_remove, mock_exists, mock_makedirs
    ):
        mock_exists.return_value = True
        recipe = {
            "package": {"cli_name": "fakebin_custom"},
            "install": {
                "executable_path": "fake_app",
                "cli_dir": "/custom/bin",
            },
        }
        main.create_cli_symlink(recipe, "/opt/fake")
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
        mock_exists.side_effect = lambda path: (
            path in (expected_local_path, os.path.dirname(expected_local_path))
        )

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
        expected_global_path = os.path.join(main.GLOBAL_RECIPES_DIR, "myrecipe.toml")
        mock_exists.side_effect = lambda path: (
            path in (expected_global_path, os.path.dirname(expected_global_path))
        )

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
            "package": {"name": "app"},
            "install": {"dir_name": "app"},
            "version": {
                "local_strategy": "file",
                "local_file": "version.txt",
                "pattern": r"app-version=([\d\.]+)",
            },
        }
        version = main.get_local_version(recipe)
        self.assertEqual(version, "4.5.6")
        expected_path = os.path.join(main.SPARK_PREFIX, "app", "version.txt")
        mock_file.assert_called_once_with(expected_path, "r")

    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data=b'version = "7.8.9"\n')
    def test_get_local_version_manifest_strategy(self, mock_file, mock_exists):
        mock_exists.return_value = True
        recipe = {
            "package": {"name": "app"},
            "install": {"dir_name": "app"},
            "version": {
                # Implicitly uses manifest default
            },
        }
        version = main.get_local_version(recipe)
        self.assertEqual(version, "7.8.9")
        expected_path = os.path.join(main.SPARK_PREFIX, "app", ".spark-manifest.toml")
        mock_file.assert_called_once_with(expected_path, "rb")

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
            recipe, "/tmp/extract", "/opt/new", "/new/icon.png", "app.desktop"
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
    @patch("spark.main.create_cli_symlink")
    @patch("spark.main.install_desktop_file")
    @patch("spark.main.ensure_not_running")
    @patch("spark.main.load_recipe")
    def test_main_match_pattern_comparison(
        self,
        mock_load_recipe,
        mock_ensure_not_running,
        mock_install_desktop,
        mock_create_cli_symlink,
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

        with patch("sys.argv", ["spark", "upgrade", "app"]):
            with self.assertRaises(SystemExit) as cm:
                main.main()
            self.assertEqual(cm.exception.code, 0)

        mock_download.assert_not_called()

    @patch("spark.main.update_repositories")
    def test_main_update(self, mock_update_repositories):
        with patch("sys.argv", ["spark", "update"]):
            with self.assertRaises(SystemExit) as cm:
                main.main()
            self.assertEqual(cm.exception.code, 0)
        mock_update_repositories.assert_called_once()

    @patch("os.makedirs")
    @patch("os.listdir")
    @patch("os.path.isdir")
    @patch("os.path.exists")
    @patch("subprocess.run")
    def test_update_repositories(
        self, mock_run, mock_exists, mock_isdir, mock_listdir, mock_makedirs
    ):
        mock_exists.return_value = True
        mock_isdir.return_value = True
        mock_listdir.return_value = ["repo1"]

        main.update_repositories()

        self.assertEqual(mock_run.call_count, 3)
        mock_run.assert_any_call(
            ["git", "-C", os.path.abspath("./config/spark/recipes/repo1"), "pull"],
            check=True,
        )
        mock_run.assert_any_call(
            [
                "git",
                "clone",
                main.CORE_REPO_URL,
                os.path.expanduser("~/.config/spark/recipes/core"),
            ],
            check=True,
        )

    @patch("shutil.rmtree")
    @patch("os.remove")
    @patch("os.path.exists")
    @patch("builtins.input")
    @patch("spark.main.load_recipe")
    def test_process_uninstall(
        self,
        mock_load_recipe,
        mock_input,
        mock_exists,
        mock_remove,
        mock_rmtree,
    ):
        mock_load_recipe.return_value = {
            "package": {"name": "app", "cli_name": "app-cli"},
            "install": {"dir_name": "app_dir"},
            "integration": {"desktop_file": "app.desktop"},
        }
        # First exists for target_dir, then cli_symlink, then desktop file
        mock_exists.return_value = True
        mock_input.return_value = "y"

        with patch("subprocess.run"):
            main.process_uninstall("app_recipe", yes=False)

        mock_rmtree.assert_called_with(os.path.join(main.SPARK_PREFIX, "app_dir"))
        mock_remove.assert_any_call(os.path.expanduser("~/.local/bin/app-cli"))
        mock_remove.assert_any_call(
            os.path.expanduser("~/.local/share/applications/app.desktop")
        )

    @patch("shutil.rmtree")
    @patch("os.remove")
    @patch("os.path.exists")
    @patch("builtins.input")
    @patch("spark.main.load_recipe")
    def test_process_uninstall_dry_run(
        self,
        mock_load_recipe,
        mock_input,
        mock_exists,
        mock_remove,
        mock_rmtree,
    ):
        mock_load_recipe.return_value = {
            "package": {"name": "app", "cli_name": "app-cli"},
            "install": {"dir_name": "app_dir"},
            "integration": {"desktop_file": "app.desktop"},
        }
        mock_exists.return_value = True
        mock_input.return_value = "y"

        with patch("subprocess.run"):
            main.process_uninstall("app_recipe", yes=False, dry_run=True)

        mock_rmtree.assert_not_called()
        mock_remove.assert_not_called()
        mock_input.assert_called_once()

    @patch("os.path.exists")
    @patch("os.listdir")
    @patch("os.path.isdir")
    @patch("spark.main.read_manifest")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_process_list(
        self,
        mock_stdout,
        mock_read_manifest,
        mock_isdir,
        mock_listdir,
        mock_exists,
    ):
        mock_exists.return_value = True
        mock_listdir.return_value = ["app1", "app2", "app3"]
        mock_isdir.return_value = True

        def side_effect_read_manifest(path):
            if "app1" in path:
                return {"recipe_name": "recipe1"}
            elif "app2" in path:
                return {}
            else:
                return None

        mock_read_manifest.side_effect = side_effect_read_manifest

        main.process_list()

        output = mock_stdout.getvalue().strip().split("\n")
        self.assertEqual(output, ["recipe1"])

    def test_process_info_empty(self):
        with patch("spark.main.SPARK_PREFIX", "/does/not/exist"):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                main.process_global_info()
                self.assertIn("0 packages, 0 files, 0B", mock_stdout.getvalue())

    def test_process_info_with_packages(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("spark.main.SPARK_PREFIX", temp_dir):
                # Create fake package dir
                app_dir = os.path.join(temp_dir, "app1")
                os.makedirs(app_dir)

                # Create fake manifest
                manifest_path = os.path.join(app_dir, ".spark-manifest.toml")
                with open(manifest_path, "w") as f:
                    f.write(
                        'package_name = "app1"\nrecipe_name = "app1-recipe"\nversion = "1.0"\n'
                    )

                # Create fake files in app dir
                file1 = os.path.join(app_dir, "file1.txt")
                with open(file1, "w") as f:
                    f.write("Hello")
                file2 = os.path.join(app_dir, "file2.txt")
                with open(file2, "w") as f:
                    f.write("World!")

                def mock_load_recipe_side_effect(name, quiet=False):
                    if name == "app1-recipe":
                        return {
                            "package": {"name": "app1", "cli_name": "app1-cli"},
                            "integration": {"desktop_file": "app1.desktop"},
                        }
                    return {}

                with patch(
                    "spark.main.load_recipe", side_effect=mock_load_recipe_side_effect
                ):
                    with patch("spark.main.get_cli_symlink_path") as mock_get_cli:
                        with patch(
                            "spark.main.get_desktop_dest_path"
                        ) as mock_get_desktop:
                            mock_get_cli.return_value = os.path.join(
                                temp_dir, "fake-cli"
                            )
                            with open(mock_get_cli.return_value, "w") as f:
                                f.write("CLI")

                            mock_get_desktop.return_value = os.path.join(
                                temp_dir, "fake.desktop"
                            )
                            with open(mock_get_desktop.return_value, "w") as f:
                                f.write("DESKTOP")

                            with patch(
                                "sys.stdout", new_callable=io.StringIO
                            ) as mock_stdout:
                                main.process_global_info()
                                output = mock_stdout.getvalue()
                                self.assertIn("1 packages, 5 files", output)
                                self.assertIn("B", output)

    @patch("spark.main.get_installed_packages")
    @patch("spark.main.process_package_info")
    def test_process_info_command_installed(
        self, mock_process_package_info, mock_get_installed_packages
    ):
        # Setup mock installed packages
        mock_get_installed_packages.return_value = ["app1", "app2"]

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            main.process_info_command(package=None, show_installed=True)
            output = mock_stdout.getvalue()

            # Since there are 2 packages, we should see one blank line
            self.assertEqual(output.count("\n"), 1)

            # verify process_package_info was called for both
            mock_process_package_info.assert_any_call("app1")
            mock_process_package_info.assert_any_call("app2")
            self.assertEqual(mock_process_package_info.call_count, 2)

    def test_get_disk_usage(self):
        mock_st = MagicMock(spec=os.stat_result)
        mock_st.st_blocks = 10
        self.assertEqual(main.get_disk_usage(mock_st), 5120)

        mock_st_no_blocks = MagicMock()
        del mock_st_no_blocks.st_blocks
        mock_st_no_blocks.st_size = 1234
        self.assertEqual(main.get_disk_usage(mock_st_no_blocks), 1234)

    def test_calculate_path_footprint_non_existent(self):
        with patch("os.path.exists", return_value=False):
            with patch("os.path.islink", return_value=False):
                self.assertEqual(main.calculate_path_footprint("/fake/path"), (0, 0))

    def test_calculate_path_footprint_file(self):
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"12345")
            temp_name = f.name

        try:
            count, size = main.calculate_path_footprint(temp_name)
            self.assertEqual(count, 1)
            self.assertGreater(size, 0)
        finally:
            os.unlink(temp_name)

    def test_calculate_path_footprint_directory(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            file1 = os.path.join(temp_dir, "file1.txt")
            file2 = os.path.join(temp_dir, "file2.txt")
            with open(file1, "w") as f:
                f.write("Hello")
            with open(file2, "w") as f:
                f.write("World!")

            count, size = main.calculate_path_footprint(temp_dir)
            self.assertEqual(count, 2)
            self.assertGreater(size, 0)

    @patch("spark.main.load_recipe")
    @patch("spark.main.get_local_version")
    @patch("spark.main.get_target_dir")
    @patch("spark.main.get_cli_symlink_path")
    @patch("spark.main.get_desktop_filename")
    @patch("spark.main.get_desktop_dest_path")
    @patch("spark.main.calculate_path_footprint")
    def test_process_package_info(
        self,
        mock_footprint,
        mock_get_desktop_dest,
        mock_get_desktop_file,
        mock_get_cli,
        mock_get_target,
        mock_get_local_version,
        mock_load_recipe,
    ):
        mock_load_recipe.return_value = {
            "package": {"name": "app1", "description": "test app"},
            "version": {"url": "https://example.com/version"},
            "_recipe_path": os.path.join(main.GLOBAL_RECIPES_DIR, "core/app1.toml"),
        }
        mock_get_local_version.return_value = "1.0.0"
        mock_get_target.return_value = "/fake/target"
        mock_get_cli.return_value = "/fake/bin/app1"
        mock_get_desktop_file.return_value = "app1.desktop"
        mock_get_desktop_dest.return_value = "/fake/desktop/app1.desktop"

        # We need os.path.exists to return true for target, cli, and desktop
        mock_footprint.side_effect = [(10, 40960), (1, 4096), (1, 4096)]

        with patch("os.path.exists", return_value=True):
            with patch("os.path.islink", return_value=True):
                with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                    main.process_package_info("app1")
                    output = mock_stdout.getvalue()
                    self.assertIn("app1", output)
                    self.assertIn("1.0.0", output)
                    self.assertIn("12 files", output)  # total files = 10 + 1 + 1 = 12
                    self.assertIn("48.0KB", output)  # total size = 49152 bytes = 48KB
                    self.assertIn("test app", output)
                    self.assertIn("https://example.com/version", output)
                    self.assertIn(f"{main.CORE_REPO_URL}/blob/main/app1.toml", output)
                    self.assertIn("Artifacts", output)

    @patch("spark.main.load_recipe")
    @patch("spark.main.get_local_version")
    def test_process_package_info_not_installed(
        self,
        mock_get_local_version,
        mock_load_recipe,
    ):
        mock_load_recipe.return_value = {
            "package": {"name": "app2", "description": "not installed app"},
            "version": {"url": "https://example.com/app2"},
            "_recipe_path": "/custom/path/app2.toml",
        }
        mock_get_local_version.return_value = None

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            main.process_package_info("app2")
            output = mock_stdout.getvalue()
            self.assertIn("app2", output)
            self.assertIn("Not installed", output)
            self.assertIn("✘", output)
            self.assertIn("not installed app", output)
            self.assertIn("https://example.com/app2", output)
            self.assertIn("/custom/path/app2.toml", output)
            self.assertNotIn("Artifacts", output)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_process_recipes(self, mock_stdout):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            local_dir = os.path.join(temp_dir, "local")
            global_dir = os.path.join(temp_dir, "global")
            core_dir = os.path.join(global_dir, "core")

            os.makedirs(local_dir)
            os.makedirs(global_dir)
            os.makedirs(core_dir)

            # Local recipe
            with open(os.path.join(local_dir, "app1.toml"), "w") as f:
                f.write(
                    '[package]\nname = "App1"\ndescription = "Local app"\n[version]\nurl = "http://app1"'
                )

            # Global recipe in a subdir
            subdir = os.path.join(global_dir, "subdir")
            os.makedirs(subdir)
            with open(os.path.join(subdir, "app2.toml"), "w") as f:
                f.write(
                    '[package]\nname = "App2"\ndescription = "Global app"\n[version]\nurl = "http://app2"'
                )

            # Core recipe
            with open(os.path.join(core_dir, "app3.toml"), "w") as f:
                f.write(
                    '[package]\nname = "App3"\ndescription = "Core app"\n[version]\nurl = "http://app3"'
                )

            with patch("spark.main.LOCAL_RECIPES_DIR", local_dir):
                with patch("spark.main.GLOBAL_RECIPES_DIR", global_dir):
                    with patch(
                        "spark.main.CORE_REPO_URL", "https://github.com/test/repo"
                    ):
                        main.process_recipes()

            output = mock_stdout.getvalue()

            self.assertIn("App1", output)
            self.assertIn("Local app", output)
            self.assertIn("http://app1", output)
            self.assertIn(os.path.join(local_dir, "app1.toml"), output)

            self.assertIn("App2", output)
            self.assertIn("Global app", output)
            self.assertIn("http://app2", output)
            self.assertIn(os.path.join(subdir, "app2.toml"), output)

            self.assertIn("App3", output)
            self.assertIn("Core app", output)
            self.assertIn("http://app3", output)
            self.assertIn("https://github.com/test/repo/blob/main/app3.toml", output)


if __name__ == "__main__":
    unittest.main()

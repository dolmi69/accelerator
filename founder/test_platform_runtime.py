"""OS boundaries and a real isolated ASGI lifecycle (also run on Windows CI)."""
import errno
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from django.test import SimpleTestCase, TestCase, override_settings

import runtime_support as runtime
from founder.models import LabSiteVersion, StartupProfile, User
from founder.services import django_builder as builder


class PlatformAdapterTests(SimpleTestCase):
    def test_windows_lock_uses_same_byte_and_unlocks_on_error(self):
        positions = []
        def locking(fd, mode, size):
            positions.append((os.lseek(fd, 0, os.SEEK_CUR), mode, size))
        fake = SimpleNamespace(locking=locking, LK_NBLCK=2, LK_UNLCK=0)
        with TemporaryDirectory() as root, patch.object(runtime, 'is_windows', return_value=True), patch.dict('sys.modules', msvcrt=fake):
            with self.assertRaisesRegex(ValueError, 'test error'):
                with runtime.file_lock(Path(root) / 'lock'):
                    raise ValueError('test error')
        self.assertEqual(positions, [(0,2,1), (0,0,1)])

    def test_busy_windows_lock_does_not_enter_critical_section(self):
        fake = SimpleNamespace(locking=MagicMock(side_effect=OSError(errno.EACCES, 'busy')),
                               LK_NBLCK=2, LK_UNLCK=0)
        with TemporaryDirectory() as root, patch.object(runtime, 'is_windows', return_value=True), patch.dict('sys.modules', msvcrt=fake):
            with self.assertRaises(TimeoutError):
                with runtime.file_lock(Path(root) / 'lock', timeout=0):
                    self.fail('Busy lock acquired')
        fake.locking.assert_called_once()

    def test_isolated_windows_environment_keeps_os_and_utf8_but_no_api_keys(self):
        with patch.object(runtime, 'is_windows', return_value=True), patch.dict(os.environ,
                SYSTEMROOT='C:\\Windows', TEMP='C:\\Temp', QWEN_API_KEY='secret', DATABASE_URL='private'):
            env = runtime.runtime_environment()
        self.assertEqual(env['SYSTEMROOT'], 'C:\\Windows')
        self.assertEqual(env['PYTHONUTF8'], '1')
        self.assertNotIn('QWEN_API_KEY', env)
        self.assertNotIn('DATABASE_URL', env)

    def test_windows_termination_waits_for_handle_and_closes_it(self):
        kernel = MagicMock()
        kernel.OpenProcess.return_value = 123
        kernel.WaitForSingleObject.side_effect = [258, 0]
        kernel.TerminateProcess.return_value = True
        with patch('ctypes.WinDLL', return_value=kernel, create=True), patch.object(os, 'kill') as kill:
            runtime.stop_windows_process(20000)
        kernel.TerminateProcess.assert_called_once_with(123, 0)
        kernel.CloseHandle.assert_called_once_with(123)
        kill.assert_not_called()

    def test_windows_liveness_probe_never_sends_a_signal_or_terminates(self):
        for state, alive in [(258, True), (0, False)]:
            with self.subTest(state=state):
                kernel = MagicMock()
                kernel.OpenProcess.return_value = 123
                kernel.WaitForSingleObject.return_value = state
                with patch('ctypes.WinDLL', return_value=kernel, create=True), \
                     patch.object(runtime, 'is_windows', return_value=True), \
                     patch.object(os, 'kill') as kill:
                    self.assertEqual(runtime.process_is_alive(20000), alive)
                kernel.OpenProcess.assert_called_once_with(0x00100000, False, 20000)
                kernel.CloseHandle.assert_called_once_with(123)
                kernel.TerminateProcess.assert_not_called()
                kill.assert_not_called()


class NativeLabRuntimeTests(TestCase):
    def test_real_launch_stop_restart_with_cyrillic_and_spaces(self):
        # Separate root and DB: never touch a developer's active website.
        with TemporaryDirectory(prefix='cofounder-') as root:
            base = Path(root) / 'Проект команды'
            base.mkdir()
            with override_settings(BASE_DIR=base, LAB_BACKEND_RUNTIME_ENABLED=True):
                owner = User.objects.create_user(username='native-runtime', email='native@example.test')
                project = StartupProfile.objects.create(owner=owner, name='Тихое место')
                version = LabSiteVersion.objects.create(startup=project, kind='django',
                    prompt='Тест', model='local', backend_modules=['accounts','registration','catalog','booking'],
                    html='<html><head></head><body><h1>Здравствуй, Бруно!</h1></body></html>')
                try:
                    builder.start_runtime(version)
                    state = builder.runtime_status(project.pk)
                    self.assertIsNotNone(state)
                    response = httpx.get(state['url'] + 'prototype/', trust_env=False)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('Здравствуй, Бруно!', response.text)
                    database = builder._directory(project.pk) / 'project' / 'db.sqlite3'
                    data = database.read_bytes()
                    builder.stop_runtime(project.pk)
                    self.assertIsNone(builder.runtime_status(project.pk))
                    self.assertEqual(database.read_bytes(), data)
                    builder.start_runtime(version)
                    self.assertIsNotNone(builder.runtime_status(project.pk))
                finally:
                    builder.stop_runtime(project.pk)

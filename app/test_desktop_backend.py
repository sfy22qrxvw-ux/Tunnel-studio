# Copyright (c) 2026 Damian Gulczynski.
"""Regression checks against the installed pywebview package, with only WinForms stubbed."""
import importlib
import sys
from types import ModuleType
import unittest
from unittest.mock import patch
import startup_support


class BackendImportTests(unittest.TestCase):
    def setUp(self):
        # Use real pywebview: its package-level guilib value shadows the submodule.
        self.webview = importlib.import_module('webview')
        self.guilib_module = importlib.import_module('webview.guilib')
        self.platforms = importlib.import_module('webview.platforms')
        self.old_forced_gui = self.guilib_module.forced_gui_

    def tearDown(self):
        self.guilib_module.forced_gui_ = self.old_forced_gui

    def check_backend(self, renderer):
        native = ModuleType('webview.platforms.winforms')
        native.renderer = renderer
        with patch.object(self.webview, 'guilib', None), \
             patch.dict(sys.modules, {'webview.platforms.winforms': native}), \
             patch.object(self.platforms, 'winforms', native, create=True), \
             patch.object(startup_support, 'configure_bridge'), \
             patch.object(sys, 'platform', 'win32'):
            result = startup_support.desktop_backend()
            self.assertIsNone(self.webview.guilib)
            self.assertEqual(self.guilib_module.forced_gui_, 'edgechromium')
            return result

    def test_actual_pywebview_import_when_package_guilib_is_none(self):
        self.assertIs(self.check_backend('edgechromium'), self.webview)

    def test_reject_legacy_renderer_with_a_specific_error(self):
        with self.assertRaisesRegex(RuntimeError, 'WebView2 Runtime was not detected'):
            self.check_backend('mshtml')


if __name__ == '__main__':
    unittest.main()

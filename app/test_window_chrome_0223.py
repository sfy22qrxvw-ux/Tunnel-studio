"""Window styling must retain OS sizing and the application's close review."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from window_chrome import WindowChrome,caption_style,palette,perform


class WindowChrome0223Tests(unittest.TestCase):
    def test_only_caption_bits_removed_from_realistic_signed_window_style(self):
        original=0x96cf0000
        changed=caption_style(original)
        self.assertEqual(changed^original,original&0x00c00000)
        for flag in (0x00040000,0x00080000,0x00020000,0x00010000):self.assertTrue(changed&flag)
        self.assertEqual(caption_style(original-2**32),changed)

    def test_close_uses_existing_destroy_path_instead_of_exiting_process(self):
        window=SimpleNamespace(destroy=Mock(),minimize=Mock(),maximize=Mock(),restore=Mock())
        perform(window,'close');window.destroy.assert_called_once_with()
        window.minimize.assert_not_called();window.maximize.assert_not_called()

    def test_invalid_action_is_not_a_native_operation(self):
        with self.assertRaises(ValueError):perform(None,'delete')

    def test_browser_and_non_windows_mode_do_not_change_native_window(self):
        with patch('window_chrome.sys.platform','linux'):
            window=SimpleNamespace();chrome=WindowChrome(window).attach()
        self.assertIsNone(chrome.panel);self.assertFalse(hasattr(window,'_tunnel_chrome'))

    def test_standard_caption_recovery_option(self):
        with patch('window_chrome.sys.platform','win32'),patch.dict('os.environ',{'TUNNEL_STUDIO_NATIVE_FRAME':'1'}):
            window=SimpleNamespace();self.assertIsNone(WindowChrome(window).attach().panel)

    def test_caption_foreground_has_readable_contrast_in_both_themes(self):
        def luminance(value):
            rgb=[int(value[i:i+2],16)/255 for i in (1,3,5)]
            linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb]
            return sum(a*b for a,b in zip(linear,(.2126,.7152,.0722)))
        for theme in ('dark','light'):
            colours=palette(theme);bg=luminance(colours['background'])
            for key in ('foreground','muted','accent'):
                fg=luminance(colours[key]);self.assertGreaterEqual((max(bg,fg)+.05)/(min(bg,fg)+.05),4.5)

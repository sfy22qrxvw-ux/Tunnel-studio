"""Keep the native operation caption and client palette in step."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock

import appearance_palettes
from desktop import DesktopBridge
from native_progress import palette,progress_summary
from ui_preferences import AppearancePreferences
from window_chrome import caption_attributes,palette as chrome_palette


class ProgressAppearance0225Tests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.pref=Path(temp.name)/'ui-preferences.json'
        self.status=Path(temp.name)/'job_status.json'

    def test_existing_theme_file_keeps_mode_and_can_add_palette(self):
        self.pref.write_text(json.dumps({'schema':1,'theme':'dark'}))
        self.assertEqual(palette(self.status)['background'],'#172333')
        prefs=AppearancePreferences(self.pref)
        self.assertEqual(prefs.get(),{'theme':'dark'})
        self.assertTrue(prefs.set('dark','graphite')['saved'])
        self.assertTrue(prefs.set('light')['saved'])
        self.assertEqual(prefs.get(),{'theme':'light','palette':'graphite'})
        self.assertIn('error',prefs.set('dark','unknown'))

    def test_desktop_propagates_resolved_palette_to_all_native_windows(self):
        publish=Mock()
        bridge=DesktopBridge(self.pref,publish)
        bridge._chrome=Mock()
        bridge._tool_manager=Mock()
        self.assertEqual(bridge.set_window_theme('dark','midnight'),{'applied':True})
        publish.assert_called_once_with('dark','midnight')
        bridge._chrome.set_theme.assert_called_once_with('dark','midnight')
        bridge._tool_manager.set_theme.assert_called_once_with('dark','midnight')

    def test_native_colours_match_caption_in_each_palette_and_mode(self):
        for mode in ('dark','light'):
            for key in appearance_palettes.LABELS:
                with self.subTest(mode=mode,palette=key):
                    c=palette(self.status,mode,key)
                    caption=chrome_palette(mode,key)
                    self.assertEqual((c['caption'],c['accent']),(caption['background'],caption['accent']))
                    attrs=caption_attributes(mode,c)
                    self.assertEqual(attrs[20],int(mode=='dark'))
                    self.assertEqual(attrs[35],int.from_bytes(bytes.fromhex(c['caption'][1:]),'little'))
                    self.assertEqual(attrs[36],int.from_bytes(bytes.fromhex(c['text'][1:]),'little'))

    def test_status_keeps_point_count_and_cancelled_state(self):
        message='Reading whole registered cloud · 449,200,000/479,167,652 original points'
        state,title,activity,count,percent,eta=progress_summary(dict(busy=True,kind='shape matching',message=message),697)
        self.assertEqual((state,title,activity),('IN PROGRESS','Finding similar shapes','Reading whole registered cloud'))
        self.assertEqual((count,percent,eta),('449,200,000/479,167,652 original points',93,'00:52'))
        self.assertEqual(progress_summary(dict(cancelled=True,message='Stopped'),698)[0],'CANCELLED')

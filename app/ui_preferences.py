# Copyright (c) 2026 Damian Gulczynski.
"""Small per-PC display preferences, independent of survey/project snapshots."""
import json
import os
from pathlib import Path
import tempfile
import threading
from appearance_palettes import DEFAULT, LABELS

THEMES = frozenset(('light', 'dark', 'system'))
MAX_BYTES = 4096


class AppearancePreferences:
    def __init__(self, path):
        self.path = Path(path) if path is not None else None
        self._lock = threading.RLock()

    def get(self):
        if self.path is None:
            return {'theme': None}
        with self._lock:
            try:
                with self.path.open('rb') as source:
                    raw = source.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    return {'theme': None}
                value = json.loads(raw)
                theme = value.get('theme') if isinstance(value, dict) else None
                palette = value.get('palette') if isinstance(value, dict) else None
                result = {'theme': theme if isinstance(theme, str) and theme in THEMES else None}
                if isinstance(palette,str) and palette in LABELS:
                    result['palette'] = palette
                return result
            except (OSError, ValueError, UnicodeError):
                return {'theme': None}

    def set(self, theme, palette=None):
        if not isinstance(theme, str) or theme not in THEMES:
            return {'error': 'Choose Light, Dark or Follow Windows.'}
        if palette is not None and (not isinstance(palette,str) or palette not in LABELS):
            return {'error': 'Choose an available colour palette.'}
        if self.path is None:
            return {'error': 'The desktop appearance preference is unavailable.'}
        temporary = None
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                palette = palette or self.get().get('palette',DEFAULT)
                data = (json.dumps({'schema': 1, 'theme': theme, 'palette': palette}, sort_keys=True) + '\n').encode('utf8')
                with tempfile.NamedTemporaryFile(mode='wb', dir=self.path.parent,
                                                 prefix='.appearance-', suffix='.tmp',
                                                 delete=False) as target:
                    temporary = Path(target.name)
                    target.write(data)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, self.path)
                temporary = None
                return {'theme': theme, 'palette': palette, 'saved': True}
            except OSError as error:
                return {'error': 'The appearance was applied but could not be remembered: ' + str(error)}
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass

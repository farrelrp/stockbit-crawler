"""
Persistent application settings stored in config_data.
"""
import json
import logging
from pathlib import Path
from typing import Dict, Any

from config import CONFIG_DIR, DEFAULT_DELAY_SECONDS, DEFAULT_LIMIT

logger = logging.getLogger(__name__)

SETTINGS_FILE = CONFIG_DIR / 'app_settings.json'
DEFAULT_PAUSE_ON_RATE_LIMIT = True


class SettingsStore:
    """Small JSON-backed settings store."""

    def __init__(self):
        self.path = Path(SETTINGS_FILE)

    def get_job_defaults(self) -> Dict[str, Any]:
        defaults = {
            'delay': DEFAULT_DELAY_SECONDS,
            'limit': DEFAULT_LIMIT,
            'pause_on_rate_limit': DEFAULT_PAUSE_ON_RATE_LIMIT,
        }
        if not self.path.exists():
            return defaults
        try:
            with open(self.path, 'r') as f:
                saved = json.load(f)
        except Exception as e:
            logger.error("Failed to load settings: %s", e)
            return defaults
        legacy_policy = saved.get('job_defaults', {}).get('rate_limit_policy')
        defaults.update({
            'delay': saved.get('job_defaults', {}).get('delay', defaults['delay']),
            'limit': saved.get('job_defaults', {}).get('limit', defaults['limit']),
            'pause_on_rate_limit': bool(saved.get('job_defaults', {}).get(
                'pause_on_rate_limit',
                legacy_policy == 'strict_fifo' if legacy_policy is not None else defaults['pause_on_rate_limit'],
            )),
        })
        return defaults

    def set_job_defaults(self, delay: float, limit: int, pause_on_rate_limit: bool) -> Dict[str, Any]:
        payload = {
            'job_defaults': {
                'delay': delay,
                'limit': limit,
                'pause_on_rate_limit': bool(pause_on_rate_limit),
            }
        }
        try:
            with open(self.path, 'w') as f:
                json.dump(payload, f)
            return {'success': True}
        except Exception as e:
            logger.error("Failed to save settings: %s", e)
            return {'success': False, 'error': str(e)}

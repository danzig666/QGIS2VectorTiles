"""
Object-storage credentials through the QGIS Authentication Manager.

Profiles store only an authentication configuration id. The keys live
encrypted in QGIS's authentication database (master password protected) and
are read just for the operation that needs them. A "Basic" configuration
holds the access key id as user name and the secret access key as password;
QGIS's "AWS S3" method (if installed) is read the same way.

When no master password is set or the user cancels the unlock, nothing is
stored in plain text: the Publish window offers session-only keys instead
(kept in memory for the open window).
"""

from typing import Optional

from .errors import PublishingError
from .providers.base import Credentials


def _manager():
    from qgis.core import QgsApplication  # pylint: disable=import-outside-toplevel
    manager = QgsApplication.authManager()
    if manager is None or manager.isDisabled():
        raise PublishingError("Q2VT_PUB_CREDENTIALS",
                              "The QGIS authentication system is disabled or unavailable.")
    return manager


def from_auth_config(authcfg: str) -> Credentials:
    """Credentials of a saved configuration (may ask for the master password)."""
    if not authcfg:
        raise PublishingError("Q2VT_PUB_CREDENTIALS", "No saved credentials are selected.")
    from qgis.core import QgsAuthMethodConfig  # pylint: disable=import-outside-toplevel
    manager = _manager()
    config = QgsAuthMethodConfig()
    ok = manager.loadAuthenticationConfig(authcfg, config, True)
    if isinstance(ok, tuple):  # some bindings return (bool, config)
        ok, config = ok[0], ok[1] if len(ok) > 1 else config
    if not ok or not config.isValid(True):
        raise PublishingError("Q2VT_PUB_CREDENTIALS",
                              "The saved credentials could not be unlocked (master password?).")
    access = config.config("username") or config.config("access_key")
    secret = config.config("password") or config.config("secret_key")
    token = config.config("session_token") or config.config("token") or ""
    if not access or not secret:
        raise PublishingError("Q2VT_PUB_CREDENTIALS",
                              "The saved credentials need an access key id (user name) and a secret "
                              "access key (password).")
    return Credentials(access, secret, token)


def store_auth_config(name: str, access_key_id: str, secret_access_key: str,
                      existing: Optional[str] = None) -> str:
    """Save keys as an encrypted "Basic" configuration; returns its id."""
    from qgis.core import QgsAuthMethodConfig  # pylint: disable=import-outside-toplevel
    manager = _manager()
    if not manager.masterPasswordIsSet() and not manager.setMasterPassword(True):
        raise PublishingError("Q2VT_PUB_CREDENTIALS",
                              "Set the QGIS master password to save credentials, or use them for "
                              "this session only.")
    config = QgsAuthMethodConfig("Basic")
    if existing:
        config.setId(existing)
    config.setName(name)
    config.setConfig("username", access_key_id)
    config.setConfig("password", secret_access_key)
    ok = manager.updateAuthenticationConfig(config) if existing else manager.storeAuthenticationConfig(config)
    if isinstance(ok, tuple):
        ok = ok[0]
    if not ok:
        raise PublishingError("Q2VT_PUB_CREDENTIALS", "The credentials could not be saved.")
    return config.id()

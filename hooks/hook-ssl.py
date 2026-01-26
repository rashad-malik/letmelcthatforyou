"""
PyInstaller runtime hook to configure SSL certificates for bundled application.

This hook ensures that HTTPS requests work correctly in the packaged executable
by setting the SSL_CERT_FILE environment variable to the bundled certifi CA bundle.
"""
import os
import sys
import logging

# Set up logging early
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('hook-ssl')


def _fix_ssl():
    """Set SSL_CERT_FILE for bundled application."""
    logger.info(f"[SSL Hook] Running SSL fix hook")
    logger.info(f"[SSL Hook] sys.frozen: {getattr(sys, 'frozen', False)}")
    logger.info(f"[SSL Hook] sys._MEIPASS: {getattr(sys, '_MEIPASS', 'NOT SET')}")

    if getattr(sys, 'frozen', False):
        # Running as bundled exe - need to set certificate paths
        logger.info("[SSL Hook] Detected frozen/bundled application")
        try:
            import certifi
            ca_bundle = certifi.where()
            logger.info(f"[SSL Hook] certifi.where() returned: {ca_bundle}")
            logger.info(f"[SSL Hook] CA bundle exists: {os.path.exists(ca_bundle)}")

            os.environ['SSL_CERT_FILE'] = ca_bundle
            os.environ['REQUESTS_CA_BUNDLE'] = ca_bundle

            logger.info(f"[SSL Hook] Set SSL_CERT_FILE={ca_bundle}")
            logger.info(f"[SSL Hook] Set REQUESTS_CA_BUNDLE={ca_bundle}")

        except ImportError as e:
            logger.error(f"[SSL Hook] certifi import failed: {e}")
        except Exception as e:
            logger.error(f"[SSL Hook] Unexpected error: {type(e).__name__}: {e}")
    else:
        logger.info("[SSL Hook] Not frozen, skipping SSL cert setup")


_fix_ssl()

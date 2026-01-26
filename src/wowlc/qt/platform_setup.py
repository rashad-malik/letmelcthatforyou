"""
Qt platform configuration for cross-platform compatibility.

This module MUST be imported before any PySide6/Qt imports to configure
the rendering environment properly on Linux systems with problematic
GL implementations.
"""
import os
import sys
import logging
import subprocess
import shutil

logger = logging.getLogger(__name__)

# Track if we've already configured
_configured = False


def _is_linux() -> bool:
    """Check if running on Linux."""
    return sys.platform.startswith('linux')


def _is_frozen() -> bool:
    """Check if running as bundled executable."""
    return getattr(sys, 'frozen', False)


def _is_nixos() -> bool:
    """Detect NixOS environment."""
    return os.path.exists('/etc/NIXOS') or os.path.isdir('/nix/store')


def _check_gl_available() -> bool:
    """
    Test if hardware GL is available and working.

    Returns True if GL appears functional, False otherwise.
    """
    # If explicitly disabled, don't check
    if os.environ.get('WOWLC_FORCE_SOFTWARE_RENDERING') == '1':
        return False

    # If already configured for software rendering (e.g., by AppRun), respect that
    if os.environ.get('QT_XCB_GL_INTEGRATION') == 'none':
        return False

    # Try glxinfo first (most reliable)
    glxinfo = shutil.which('glxinfo')
    if glxinfo:
        try:
            result = subprocess.run(
                [glxinfo],
                capture_output=True,
                timeout=5,
                env={**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')}
            )
            if result.returncode == 0:
                output = result.stdout.decode('utf-8', errors='ignore')
                # Check for software renderer indicators
                if 'llvmpipe' in output.lower() or 'softpipe' in output.lower():
                    logger.info("GL check: Software renderer detected via glxinfo")
                    return False
                logger.info("GL check: Hardware GL appears available")
                return True
            else:
                logger.warning(f"GL check: glxinfo failed with code {result.returncode}")
                return False
        except subprocess.TimeoutExpired:
            logger.warning("GL check: glxinfo timed out")
            return False
        except Exception as e:
            logger.warning(f"GL check: glxinfo error: {e}")
            return False

    # Fallback: check for DRI device
    if os.path.exists('/dev/dri'):
        try:
            dri_devices = os.listdir('/dev/dri')
            if any(d.startswith('card') for d in dri_devices):
                logger.info("GL check: DRI devices present, assuming GL available")
                return True
        except Exception:
            pass

    logger.warning("GL check: No GL indicators found")
    return False


def configure_qt_platform():
    """
    Configure Qt environment variables for the current platform.

    This function should be called BEFORE any Qt/PySide6 imports.
    It sets environment variables to handle GL rendering issues on Linux.
    """
    global _configured

    # Only configure once
    if _configured:
        return
    _configured = True

    if not _is_linux():
        logger.debug("Qt platform setup: Not Linux, skipping")
        return

    logger.info("Qt platform setup: Configuring for Linux")

    # Always set these for bundled apps
    if _is_frozen():
        os.environ.setdefault('QTWEBENGINE_DISABLE_SANDBOX', '1')
        os.environ.setdefault('QT_QPA_PLATFORM', 'xcb')

    # Determine if we need software rendering
    use_software = False
    reason = ""

    # Check 1: Explicit user request
    if os.environ.get('WOWLC_FORCE_SOFTWARE_RENDERING') == '1':
        use_software = True
        reason = "user requested via WOWLC_FORCE_SOFTWARE_RENDERING"

    # Check 2: NixOS (known to have issues)
    elif _is_nixos():
        use_software = True
        reason = "NixOS detected"

    # Check 3: Environment already configured (from AppRun)
    elif os.environ.get('QT_XCB_GL_INTEGRATION') == 'none':
        use_software = True
        reason = "already configured in environment"

    # Check 4: Probe GL availability
    elif not _check_gl_available():
        use_software = True
        reason = "GL check failed"

    if use_software:
        logger.info(f"Qt platform setup: Enabling software rendering ({reason})")
        _enable_software_rendering()
    else:
        logger.info("Qt platform setup: Using hardware GL")


def _enable_software_rendering():
    """Configure environment for software rendering."""
    # Core GL settings
    os.environ['QT_XCB_GL_INTEGRATION'] = 'none'
    os.environ['LIBGL_ALWAYS_SOFTWARE'] = '1'

    # Qt Quick backend
    os.environ['QT_QUICK_BACKEND'] = 'software'

    # Chromium/WebEngine flags
    chromium_flags = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')
    new_flags = [
        '--disable-gpu',
        '--disable-gpu-compositing',
    ]
    for flag in new_flags:
        if flag not in chromium_flags:
            chromium_flags = f"{chromium_flags} {flag}".strip()
    os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = chromium_flags

    logger.info("Qt platform setup: Software rendering configured")
    logger.debug(f"  QT_XCB_GL_INTEGRATION={os.environ.get('QT_XCB_GL_INTEGRATION')}")
    logger.debug(f"  LIBGL_ALWAYS_SOFTWARE={os.environ.get('LIBGL_ALWAYS_SOFTWARE')}")
    logger.debug(f"  QT_QUICK_BACKEND={os.environ.get('QT_QUICK_BACKEND')}")
    logger.debug(f"  QTWEBENGINE_CHROMIUM_FLAGS={os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS')}")

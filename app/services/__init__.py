"""
Rosetta Magazine Researcher
Application factory and initialization logic.
"""

import logging
import ssl
import sys
import time
import webbrowser
from pathlib import Path

import certifi
from flask import Flask
from werkzeug.serving import is_running_from_reloader

from app import config as cfg
from app.routes import api, pages

# --- Global Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Fix for Linux/Mac SSL certificate errors when fetching catalogs
ssl._create_default_https_context = lambda: ssl.create_default_context(
    cafile=certifi.where()
)

def create_app() -> Flask:
    """
    Creates and configures the Flask application instance.
    
    Returns:
        Flask: The configured app ready to run.
    """
    app = Flask(__name__)
    
    # Verify UI folders exist (Step 3: Folder Verification)
    # PyInstaller builds often fail if these aren't bundled correctly.
    template_dir = Path(app.template_folder)
    static_dir = Path(app.static_folder)
    
    if not template_dir.exists() or not static_dir.exists():
        logger.critical(f"UI Folders missing! (Templates: {template_dir.exists()}, Static: {static_dir.exists()})")
        logger.critical("If running from a build, ensure 'app/templates' and 'app/static' are bundled.")
    
    if cfg.server_dev_mode():
        logger.info("--- DEV MODE ACTIVE: Templates will auto-reload, browser cache disabled ---")
        app.config["TEMPLATES_AUTO_RELOAD"] = True
        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    
    # Register Route Blueprints
    app.register_blueprint(pages.bp)
    app.register_blueprint(api.bp, url_prefix="/api")
    
    return app

def run_app() -> None:
    """
    Starts the Rosetta server, background monitors, and opens the browser.
    """
    from app.services import metadata, state

    dev_mode = cfg.server_dev_mode()
    port = cfg.server_port()
    app = create_app()

    # Avoid running startup logic twice when Flask reloader triggers
    should_run_startup = not dev_mode or is_running_from_reloader()

    if should_run_startup:
        logger.info(f"Starting Rosetta Magazine Researcher (v1.0.0)")
        metadata.load_metadata_cache()
        
        # Step 4: Release Safety Check
        if not dev_mode:
            logger.info("Heartbeat monitor active. App will close 20s after browser tab is closed.")
            state.start_heartbeat_monitor()
            
            # Auto-open browser in release mode
            time.sleep(1.5)
            url = f"http://127.0.0.1:{port}"
            logger.info(f"Opening browser at {url}")
            webbrowser.open(url)
        else:
            logger.warning("Running in DEBUG mode. Heartbeat monitor is disabled.")

    app.run(
        port=port,
        debug=dev_mode,
        use_reloader=dev_mode,
    )
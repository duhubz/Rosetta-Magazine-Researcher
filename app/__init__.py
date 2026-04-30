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
# MAC FIX: When running as a .app bundle, sys.stdout might be None. 
# We check this to prevent the app from crashing on startup.
logging_handlers = [logging.StreamHandler(sys.stdout)] if sys.stdout is not None else []

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=logging_handlers
)
logger = logging.getLogger(__name__)

# Fix for Linux/Mac SSL certificate errors when fetching catalogs
ssl._create_default_https_context = lambda: ssl.create_default_context(
    cafile=certifi.where()
)

def create_app() -> Flask:
    """
    Creates and configures the Flask application instance.
    """
    app = Flask(__name__)
    
    # Verify UI folders exist
    # If these are missing, the app will show a blank screen or 404 errors.
    template_dir = Path(app.template_folder)
    static_dir = Path(app.static_folder)
    
    if not template_dir.exists() or not static_dir.exists():
        logger.critical(f"UI Folders missing! (Templates: {template_dir.exists()}, Static: {static_dir.exists()})")
        logger.critical("Check your PyInstaller data-add paths if running from a build.")
    
    if cfg.server_dev_mode():
        logger.info("--- DEV MODE ACTIVE: Templates will auto-reload, browser cache disabled ---")
        app.config["TEMPLATES_AUTO_RELOAD"] = True
        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    
    # Register blueprints
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

    # Avoid running startup logic twice when Flask reloader triggers in dev mode
    should_run_startup = not dev_mode or is_running_from_reloader()
    should_open_browser = not dev_mode

    if should_run_startup:
        logger.info("Starting Rosetta Magazine Researcher")
        metadata.load_metadata_cache()
        
        if not dev_mode:
            logger.info("Heartbeat monitor active.")
            state.start_heartbeat_monitor()

    if should_open_browser:
        # Give the server a second to warm up before opening browser
        time.sleep(1.5)
        url = f"http://127.0.0.1:{port}"
        logger.info(f"Opening browser at {url}")
        webbrowser.open(url)

    app.run(
        port=port,
        debug=dev_mode,
        use_reloader=dev_mode,
    )
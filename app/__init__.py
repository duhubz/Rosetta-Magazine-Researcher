"""Rosetta Magazine Researcher - Archive viewer with search and community catalogs."""

import ssl
import time
import webbrowser
import shutil

import certifi
from flask import Flask
from werkzeug.serving import is_running_from_reloader

from app import config as cfg
from app.routes import api, pages

# Fix for Linux/Mac SSL certificate errors
ssl._create_default_https_context = lambda: ssl.create_default_context(
    cafile=certifi.where()
)


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    if cfg.server_dev_mode():
        app.config["TEMPLATES_AUTO_RELOAD"] = True
        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.register_blueprint(pages.bp)
    app.register_blueprint(api.bp, url_prefix="/api")
    return app


def run_app() -> None:
    from app.services import metadata, state

    dev_mode = cfg.server_dev_mode()
    port = cfg.server_port()
    app = create_app()

    should_run_startup = not dev_mode or is_running_from_reloader()
    should_open_browser = not dev_mode

    if should_run_startup:
        metadata.load_metadata_cache()
        if not dev_mode:
            state.start_heartbeat_monitor()

    if should_open_browser:
        time.sleep(1)
        webbrowser.open(f"http://127.0.0.1:{port}")

    app.run(
        port=port,
        debug=dev_mode,
        use_reloader=dev_mode,
    )

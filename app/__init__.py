"""Rosetta Magazine Researcher - Archive viewer with search and community catalogs."""

import ssl
import time
import webbrowser

import certifi
from flask import Flask

from app import config as cfg
from app.routes import api, pages

# Fix for Linux/Mac SSL certificate errors
ssl._create_default_https_context = lambda: ssl.create_default_context(
    cafile=certifi.where()
)


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.register_blueprint(pages.bp)
    app.register_blueprint(api.bp, url_prefix="/api")
    return app


def run_app() -> None:
    """Start the application server and open browser."""
    from app.services import metadata, state

    metadata.load_metadata_cache()
    state.start_heartbeat_monitor()
    time.sleep(1)

    port = cfg.server_port()
    webbrowser.open(f"http://127.0.0.1:{port}")
    app = create_app()
    app.run(port=port, debug=False)

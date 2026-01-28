import logging
import argparse
from os import wait

from pathlib import Path

from flask import Flask, send_from_directory

log = logging.getLogger(__name__)
from .utils import validate_path

STATIC_DIR = Path(__file__).with_name("web")

def build_view_parser(parser: argparse._SubParsersAction):
    view_parser = parser.add_parser(
        'view',
        help='Then, use "view" over the output directory to serve the files '
            'through a webserver.'
    )
    view_parser.add_argument(
        "output_dir", type=validate_path,
        help="Path to processed output dir (the one containing metadata.json)"
    )
    view_parser.add_argument("--host", default="127.0.0.1")
    view_parser.add_argument("--port", type=int, default=5000)
    view_parser.add_argument("--debug", action="store_true")

def create_app(output_dir: Path) -> Flask:
    # app = Flask(__name__, static_folder=STATIC_DIR)
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='')

    # UI
    @app.get("/")
    def index():
        return send_from_directory(str(app.static_folder), "index.html")

    return app

def rm_view(args: argparse.Namespace):
    output_dir = Path(args.output_dir)
    app = create_app(output_dir)
    log.info(f"Serving {output_dir} on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)

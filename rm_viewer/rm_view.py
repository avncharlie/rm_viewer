import argparse

from .utils import validate_path, validate_output_path

def build_view_parser(parser: argparse._SubParsersAction):
    view_parser = parser.add_parser(
        'view',
        help='Then, use "view" over the output directory to serve the files '
            'through a webserver.'
    )

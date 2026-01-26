import os
import json
import shutil
import hashlib
import logging
import argparse
import tempfile
import traceback
from pathlib import Path

import fitz
import xxhash
from remarks import run_remarks
from rmc.exporters.svg import set_device, set_dimensions_for_pdf
from rmc.exporters.pdf import rm_to_pdf

from .utils import validate_path, validate_output_path, get_gcv_api_key
from .ocr import run_ocr_on_rm_output, add_text_layer_to_page

from .rm_items import RemarkableItem, RemarkableFolder, RemarkableBook

log = logging.getLogger(__name__)

def xx_dir_hash(directory: Path) -> str:
    """Compute a hash of all files in directory for change detection."""
    h = xxhash.xxh3_64()
    for f in sorted(directory.rglob('*')):
        if f.is_file():
            h.update(str(f.relative_to(directory)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def call_remarks(xochitl_dir: Path, output_dir: Path) -> bool:
    """Run remarks on xochitl directory. Returns True on success."""
    log.info(f"Running remarks on {xochitl_dir}")
    try:
        run_remarks(xochitl_dir, output_dir)
        return True
    except Exception as e:
        log.warning(f"remarks failed: {e}")
        return False


def build_process_parser(parser: argparse._SubParsersAction):
    process_parser = parser.add_parser(
        'processor',
        help='Run "processor" to convert a Remarkable file directory into an '
            'output directory.'
    )
    process_parser.add_argument(
        'xochitl_dir', type=validate_path,
        help="Path of xochitl dir to process"
    )
    process_parser.add_argument(
        'output_dir', type=validate_output_path,
        help="Path to put processed xochitl files"
    )
    process_parser.add_argument(
        '--ocr-debug', action='store_true',
        help="Make OCR text visible for debugging alignment"
    )


def create_id_filemap(xochitl_dir: Path) -> dict[str, list[Path]]:
    '''
    Group files by ID

    :param xochitl_dir: /home/root/.local/share/remarkable/xochitl directory
    from remarkable
    :returns: mapping of notebook ID -> files corresponding to notebook
    '''
    xochitl_files = os.listdir(xochitl_dir)
    id_filemap: dict[str, list[Path]] = {}
    for filename in xochitl_files:
        uuid = filename.split('.')[0]
        if uuid not in id_filemap:
            id_filemap[uuid] = []
        id_filemap[uuid].append(xochitl_dir / filename)
    return id_filemap

def get_page_count(content: dict) -> int:
    """Extract total page count from content.json."""
    if 'cPages' in content:
        return len(content['cPages'].get('pages', []))
    elif 'pages' in content:
        return len(content['pages'])
    return 0


def get_pages(content: dict) -> list[str]:
    """
    Extract ordered list of page IDs from content
    :param content: parsed .content
    :returns: List of page IDs in display order
    """
    if "cPages" in content:
        return [
            page["id"] for page in content["cPages"]["pages"]
            if not page.get("deleted", {}).get("value", 0) == 1
        ]
    elif "pages" in content:
        return content["pages"]
    return []


def get_page_redir_map(content: dict) -> dict[str, int | None]:
    """
    Get mapping from page ID to backing PDF page index.

    :param content: parsed .content
    :returns: Dict mapping page_id -> backing PDF page index, or None if inserted page
    """
    redir_map = {}
    if "cPages" in content:
        for page in content["cPages"]["pages"]:
            if page.get("deleted", {}).get("value", 0) == 1:
                continue
            page_id = page["id"]
            if "redir" in page:
                redir_map[page_id] = page["redir"].get("value")
            else:
                # Inserted page - no backing PDF
                redir_map[page_id] = None
    elif "pages" in content:
        # Old format - assume 1:1 mapping
        for i, page_id in enumerate(content["pages"]):
            redir_map[page_id] = i
    return redir_map


def build_rm_file_index(
    rm_file_dir: Path,
    rm_output_dir: Path,
    base_output_dir: Path,
    pages: list[str],
    content: dict,
    backing_pdf_file: Path | None,
    api_key: str | None = None
) -> list[dict]:
    '''
    Build index of .rm files with their page mappings and convert to PDF.

    :param rm_file_dir: Directory containing .rm files
    :param rm_output_dir: Directory for converted PDFs
    :param base_output_dir: Base output directory for relative paths
    :param pages: Ordered list of page IDs
    :param content: Parsed .content dict
    :param backing_pdf_file: Path to backing PDF, or None
    :returns: List of dicts with path, index, backing_pdf_index
    '''
    rm_files = []
    redir_map = get_page_redir_map(content)

    # Open backing PDF if it exists to get page dimensions
    backing_pdf_doc = None
    if backing_pdf_file and backing_pdf_file.exists():
        backing_pdf_doc = fitz.open(backing_pdf_file)

    for f in rm_file_dir.rglob('*.rm'):
        page_id = f.stem
        page_index = pages.index(page_id)
        backing_pdf_index = redir_map.get(page_id)

        # Set dimensions based on backing PDF page or device default
        if backing_pdf_doc and backing_pdf_index is not None:
            page = backing_pdf_doc[backing_pdf_index]
            w_pt, h_pt = page.rect.width, page.rect.height
            set_dimensions_for_pdf(w_pt, h_pt)
        else:
            set_device('RMPP')

        # Convert .rm to PDF
        fname = f'{page_index} - {page_id}'
        rm_output_pdf = rm_output_dir / f'{fname}.pdf'
        rm_to_pdf(str(f), str(rm_output_pdf))

        rm_hash = hashlib.md5(f.read_bytes()).hexdigest()

        # Run OCR if API key is available
        ocr_path = None
        if api_key:
            ocr_json_path = rm_output_dir / f'{fname}.ocr.json'
            ocr_result = run_ocr_on_rm_output(
                rm_output_pdf, ocr_json_path, api_key, rm_hash
            )
            if ocr_result:
                ocr_path = str(ocr_json_path.relative_to(base_output_dir))

        rm_files.append({
            'rm_path': str(f.relative_to(base_output_dir)),
            'rm_hash': rm_hash,
            'out_path': str(rm_output_pdf.relative_to(base_output_dir)),
            'index': page_index,
            'backing_pdf_index': backing_pdf_index,
            'ocr_path': ocr_path
        })

    if backing_pdf_doc:
        backing_pdf_doc.close()

    return rm_files


def stitch_ocr_text_layers(
    output_pdf: Path,
    rm_files: list[dict],
    base_output_dir: Path,
    debug: bool = False
) -> int:
    """
    Stitch OCR text layers into the final remarks PDF.

    :param output_pdf: Path to the remarks output PDF
    :param rm_files: List of rm file dicts with ocr_path entries
    :param base_output_dir: Base output directory for resolving relative paths
    :param debug: If True, make text visible for debugging
    :returns: Number of pages with OCR text added
    """
    doc = fitz.open(output_pdf)
    pages_with_ocr = 0

    for rm_file in rm_files:
        ocr_rel_path = rm_file.get('ocr_path')
        if not ocr_rel_path:
            continue

        page_index = rm_file['index']
        if page_index >= len(doc):
            log.warning(f"Page index {page_index} out of range for {output_pdf}")
            continue

        page = doc[page_index]

        ocr_path = base_output_dir / ocr_rel_path
        if not ocr_path.exists():
            log.warning(f"OCR file not found: {ocr_path}")
            continue

        try:
            with open(ocr_path) as f:
                ocr_result = json.load(f)

            words_added = add_text_layer_to_page(
                page, ocr_result, page.rect.width, page.rect.height, debug=debug
            )

            if words_added > 0:
                pages_with_ocr += 1
                log.debug(f"Added {words_added} words to page {page_index}")

        except Exception as e:
            log.warning(f"Failed to add OCR layer for page {page_index}: {e}")

    doc.save(output_pdf, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()

    return pages_with_ocr


def parse_item(id: str, files: list[Path], output_dir: Path, api_key: str | None = None, ocr_debug: bool = False) -> dict:
    '''
    Given item ID and xochitl files, generate output folder containing
    thumbnails, output pdf and extracted text. Returns metadata for item.

    :param id: UUID of item
    :param files: xochitl files corresponding to item
    :param out_dir: directory to put output
    :param out_dir: directory to put output
    '''
    # Get metadata and content
    metadata = {}
    content = {}

    pages: list[str] = []
    last_opened_page = 1
    parent = ''
    name = ''
    for file in files:
        if str(file).endswith('.metadata'):
            with open(file) as f:
                metadata = json.load(f)
                name = metadata.get('visibleName', '')
                parent = metadata.get('parent', None)
        if str(file).endswith('.content'):
            with open(file) as f:
                content = json.load(f)
                pages = get_pages(content)
                last = content.get('cPages', {})\
                    .get('lastOpened', {}).get('value', '')
                if last in pages:
                    last_opened_page = pages.index(last) + 1

    # if name != 'Writing test':
    # if name != 'Getting started copy':
    # if name != 'Systematic Theology notes':
    if name != 'Colours':
    # if name != 'Everybody, always':
    # if name != 'Fair Play':
        return {}

    # Skip empty items
    if not content and not metadata:
        return {}

    # Skip deleted items
    if 'parent' in metadata and metadata['parent'] == 'trash':
        log.info(f'Skipping deleted item: {name}')
        return {}

    # Return info for folders
    is_folder = len(files) == 1 or not content
    if is_folder:
        return {
            'type': 'folder',
            'id': id,
            'name': name,
            'parent': parent
        }

    log.info(f'Processing item: {name}')

    # Create output directories
    nb_output_dir = output_dir / name
    nb_xochitl_dir = nb_output_dir / 'xochitl'
    nb_thumbnail_dir = nb_output_dir / 'thumbnails'
    nb_rm_output_dir = nb_output_dir / 'rm_output'
    nb_output_dir.mkdir(parents=True, exist_ok=True)
    nb_xochitl_dir.mkdir(exist_ok=True)
    nb_thumbnail_dir.mkdir(exist_ok=True)
    nb_rm_output_dir.mkdir(exist_ok=True)

    # Copy xochitl files into nb_xochitl_dir
    rm_file_dir = None
    for file in files:
        if file.is_dir():
            cpdir = nb_xochitl_dir / file.name
            shutil.copytree(file, cpdir)
            if file.name == id:
                rm_file_dir = cpdir
        else:
            shutil.copy(file, nb_xochitl_dir)

    # Run remarks in temp directory
    output_pdf = nb_output_dir / 'out.pdf'
    with tempfile.TemporaryDirectory() as tmp_dir:
        remarks_out = Path(tmp_dir) / 'remarks_out'
        run_remarks(nb_xochitl_dir, remarks_out)
        expected_pdf = remarks_out / f'{name} _remarks.pdf'
        if not expected_pdf.exists():
            raise RuntimeError(f'Remarks produced no output for item "{name}"')
        shutil.copy2(expected_pdf, output_pdf)

    # Get backing PDF
    backing_pdf = None
    backing_pdf_file = nb_xochitl_dir / f'{id}.pdf'
    if backing_pdf_file.exists():
        backing_pdf = backing_pdf_file

    # Build rm_file index (with OCR if api_key available)
    rm_files = []
    if rm_file_dir:
        rm_files = build_rm_file_index(
            rm_file_dir, nb_rm_output_dir, output_dir, pages, content, backing_pdf_file,
            api_key=api_key
        )

    # Stitch OCR text layers into the final PDF
    if rm_files:
        stitch_ocr_text_layers(output_pdf, rm_files, output_dir, debug=ocr_debug)

    print(json.dumps(rm_files, indent=2))

    xochitl_dir = str(nb_xochitl_dir.relative_to(output_dir))
    output_pdf = str(output_pdf.relative_to(output_dir))
    backing_pdf = '' if not backing_pdf else str(backing_pdf.relative_to(output_dir))
    thumbnail_dir = str(nb_thumbnail_dir.relative_to(output_dir))

    dir_hash = xx_dir_hash(nb_xochitl_dir)

    return {
        'type': 'book',
        'id': id,
        'name': name,
        'parent': parent,

        'xochitl_dir': xochitl_dir,
        'output_pdf': output_pdf,
        'backing_pdf': backing_pdf,
        'thumbnail_dir': thumbnail_dir,

        'dir_hash': dir_hash,

        'rm_files': rm_files,

        'last_opened_page': last_opened_page,
        'total_pages': len(pages)
    }

def try_get_name(files: list[Path]):
    for file in files:
        if str(file).endswith('.metadata'):
            with open(file) as f:
                metadata = json.load(f)
                name = metadata.get('visibleName', '')
                return name

def rm_process(args: argparse.Namespace):
    xochitl_dir = Path(args.xochitl_dir)
    output_dir = Path(args.output_dir)
    ocr_debug = getattr(args, 'ocr_debug', False)

    # Get GCV API key for OCR
    api_key = get_gcv_api_key()
    if api_key:
        log.info("GCV API key found, OCR will be enabled")
        if ocr_debug:
            log.info("OCR debug mode enabled - text will be visible")
    else:
        log.info("No GCV API key found, OCR will be disabled")

    full_metadata = []
    errors = []

    id_filemap = create_id_filemap(xochitl_dir)
    for id, files in id_filemap.items():
        try:
            metadata = parse_item(id, files, output_dir, api_key=api_key, ocr_debug=ocr_debug)
            if metadata:
                full_metadata.append(metadata)
        except Exception:
            name = try_get_name(files)
            tb = traceback.format_exc()
            errors.append({'name': name, 'id': id, 'error' : tb})
            log.error(f'Item "{name}" (UUID: {id}) failed to parse! Traceback:\n{traceback.format_exc()}')

    metadata_path = output_dir / 'metadata.json'
    with open(metadata_path, 'w') as f:
        json.dump(full_metadata, f, indent=2)

    if errors:
        errors_path = output_dir / 'errors.json'
        with open(errors_path, 'w') as f:
            json.dump(errors, f, indent=2)

    log.info(f'Processed {len(full_metadata)} items with {len(errors)} errors')

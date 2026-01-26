import json
import base64
import logging
import math
from pathlib import Path
from datetime import datetime

import fitz
import requests

log = logging.getLogger(__name__)


def call_gcv_api(image_path: Path, api_key: str) -> dict | None:
    """
    Call Google Cloud Vision TEXT_DETECTION API on an image.

    :param image_path: Path to the image file
    :param api_key: Google Cloud Vision API key
    :returns: Raw GCV API response dict, or None on failure
    """
    # Read and base64 encode the image
    with open(image_path, 'rb') as f:
        image_data = base64.b64encode(f.read()).decode('utf-8')

    # Build the API request
    request_body = {
        "requests": [{
            "image": {"content": image_data},
            "features": {"type": "TEXT_DETECTION"},
            "imageContext": {"languageHints": ["en"]}
        }]
    }

    url = f"https://vision.googleapis.com/v1/images:annotate?key={api_key}"

    try:
        response = requests.post(
            url,
            json=request_body,
            headers={"Content-Type": "application/json"},
            timeout=60
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        log.warning(f"GCV API call failed: {e}")
        return None


def pdf_to_image(pdf_path: Path, output_path: Path, dpi: int = 300) -> tuple[int, int]:
    """
    Convert a PDF page to a PNG image.

    :param pdf_path: Path to the PDF file
    :param output_path: Path for the output PNG image
    :param dpi: Resolution for the output image (default 300)
    :returns: (width_px, height_px) of the generated image
    """
    doc = fitz.open(pdf_path)
    page = doc[0]  # Get first page

    # Calculate zoom factor for desired DPI (PDF default is 72 DPI)
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)

    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    pix.save(output_path)

    width_px, height_px = pix.width, pix.height

    pix = None  # Free memory
    doc.close()

    return width_px, height_px


def run_ocr_on_rm_output(
    rm_output_pdf: Path,
    ocr_json_path: Path,
    api_key: str,
    rm_hash: str,
    dpi: int = 300
) -> dict | None:
    """
    Run OCR on an rm output PDF and save results as JSON.

    Creates a temporary PNG, runs OCR, deletes the PNG, and saves results.
    The rm_hash is stored in the output for future caching support.

    :param rm_output_pdf: Path to the converted .rm PDF
    :param ocr_json_path: Path to save the OCR JSON result
    :param api_key: Google Cloud Vision API key
    :param rm_hash: MD5 hash of the source .rm file
    :param dpi: DPI for image conversion (default 300)
    :returns: OCR result dict, or None on failure
    """
    # Get PDF dimensions
    doc = fitz.open(rm_output_pdf)
    page = doc[0]
    pdf_width_pt = page.rect.width
    pdf_height_pt = page.rect.height
    doc.close()

    # Create temp image path (same location as output, with .png extension)
    temp_image_path = ocr_json_path.with_suffix('.png')

    try:
        # Convert PDF to image
        img_width_px, img_height_px = pdf_to_image(rm_output_pdf, temp_image_path, dpi)

        # Call GCV API
        log.info(f"Sending OCR request for: {rm_output_pdf}")
        gcv_response = call_gcv_api(temp_image_path, api_key)

        if gcv_response is None:
            log.warning(f"OCR failed for {rm_output_pdf}")
            return None

        # Build the OCR result with metadata
        ocr_result = {
            "rm_hash": rm_hash,
            "pdf_width_pt": pdf_width_pt,
            "pdf_height_pt": pdf_height_pt,
            "img_width_px": img_width_px,
            "img_height_px": img_height_px,
            "dpi": dpi,
            "timestamp": datetime.now().isoformat(),
            "gcv_response": gcv_response
        }

        # Save to JSON
        with open(ocr_json_path, 'w') as f:
            json.dump(ocr_result, f, indent=2)

        log.info(f"OCR completed for {rm_output_pdf.name}")
        return ocr_result

    finally:
        # Always clean up temp image
        if temp_image_path.exists():
            temp_image_path.unlink()


def get_text_geometry(vertices: list[dict]) -> dict | None:
    """
    Calculate text geometry from Google Vision bounding box vertices.

    Google Vision provides vertices in reading order:
    [0] = start of text (top-left in reading direction)
    [1] = end of text (top-right in reading direction)
    [2] = bottom-right
    [3] = bottom-left

    v[0] -> v[1] = text direction (length)
    v[0] -> v[3] = perpendicular (height)

    :param vertices: List of vertex dicts with 'x' and 'y' keys
    :returns: dict with angle, box_width, box_height, baseline_point, or None if invalid
    """
    if len(vertices) < 4:
        return None

    # Vector along text direction (v0 to v1)
    dx_text = vertices[1].get('x', 0) - vertices[0].get('x', 0)
    dy_text = vertices[1].get('y', 0) - vertices[0].get('y', 0)
    box_width = math.sqrt(dx_text**2 + dy_text**2)

    # Vector perpendicular to text (v0 to v3) - this is the character height direction
    dx_perp = vertices[3].get('x', 0) - vertices[0].get('x', 0)
    dy_perp = vertices[3].get('y', 0) - vertices[0].get('y', 0)
    box_height = math.sqrt(dx_perp**2 + dy_perp**2)

    # Angle of text direction in image coordinates
    # atan2(dy, dx) gives angle from positive x-axis
    image_angle = math.degrees(math.atan2(dy_text, dx_text))

    # Negate for PDF coordinate system (Y increases downward in image, upward in PDF math)
    pdf_angle = -image_angle

    # Calculate baseline insertion point
    # PDF text is positioned at the baseline, so offset from v[0] toward v[3]
    # by approximately 75% of the height
    baseline_ratio = 0.75
    baseline_x = vertices[0].get('x', 0) + dx_perp * baseline_ratio
    baseline_y = vertices[0].get('y', 0) + dy_perp * baseline_ratio

    return {
        'angle': pdf_angle,
        'box_width': box_width,
        'box_height': box_height,
        'baseline_point': (baseline_x, baseline_y),
    }


def add_text_layer_to_page(
    page: fitz.Page,
    ocr_result: dict,
    target_width_pt: float,
    target_height_pt: float,
    debug: bool = False
) -> int:
    """
    Add invisible text layer to a PDF page from OCR results.

    Handles coordinate transformation from image pixels to PDF points.

    :param page: PyMuPDF page object to add text to
    :param ocr_result: OCR result dict with gcv_response and dimensions
    :param target_width_pt: Width of the target page in points
    :param target_height_pt: Height of the target page in points
    :param debug: If True, make text visible for debugging
    :returns: Number of words added
    """
    # Extract dimensions from OCR result
    img_width_px = ocr_result['img_width_px']
    img_height_px = ocr_result['img_height_px']
    pdf_width_pt = ocr_result['pdf_width_pt']
    pdf_height_pt = ocr_result['pdf_height_pt']

    # Calculate scale factors
    # First: image pixels to source PDF points
    scale_x = pdf_width_pt / img_width_px
    scale_y = pdf_height_pt / img_height_px

    # Second: source PDF points to target page points
    final_scale_x = target_width_pt / pdf_width_pt
    final_scale_y = target_height_pt / pdf_height_pt

    # Combined scale
    total_scale_x = scale_x * final_scale_x
    total_scale_y = scale_y * final_scale_y

    # Get word-level annotations from GCV response
    gcv_response = ocr_result.get('gcv_response', {})
    responses = gcv_response.get('responses', [{}])
    text_annotations = responses[0].get('textAnnotations', [])
    words = text_annotations[1:] if len(text_annotations) > 1 else []

    font = fitz.Font("helv")
    words_added = 0

    for word_data in words:
        text = word_data.get('description', '')
        vertices = word_data.get('boundingPoly', {}).get('vertices', [])

        if len(vertices) < 4 or not text.strip():
            continue

        # Get text geometry in image pixel coordinates
        geometry = get_text_geometry(vertices)
        if geometry is None:
            continue

        pdf_angle = geometry['angle']
        insert_x_px, insert_y_px = geometry['baseline_point']

        # Transform coordinates to target page points
        insert_x = insert_x_px * total_scale_x
        insert_y = insert_y_px * total_scale_y

        # Scale box dimensions to target page points
        box_width = geometry['box_width'] * total_scale_x
        box_height = geometry['box_height'] * total_scale_y

        # Height-based font sizing
        fontsize = box_height * 0.75
        fontsize = max(fontsize, 6)

        # Calculate horizontal scale to fit box width exactly
        natural_width = font.text_length(text, fontsize=fontsize)
        if natural_width > 0:
            h_scale = box_width / natural_width
        else:
            h_scale = 1.0

        # Debug mode: draw bounding box
        if debug:
            points = [
                (v.get('x', 0) * total_scale_x, v.get('y', 0) * total_scale_y)
                for v in vertices
            ]
            shape = page.new_shape()
            shape.draw_polyline(points + [points[0]])
            shape.finish(color=(1, 0, 0), width=0.5)  # Red outline
            shape.commit()

        # Insert text with combined scale and rotation matrix
        insert_pt = fitz.Point(insert_x, insert_y)
        render_mode = 0 if debug else 3  # 0 = visible, 3 = invisible
        text_color = (0, 0, 1) if debug else None  # Blue for debug

        # Build combined transformation matrix (scale + rotation)
        scale_matrix = fitz.Matrix(h_scale, 0, 0, 1, 0, 0)
        rot_matrix = fitz.Matrix(1, 0, 0, 1, 0, 0).prerotate(pdf_angle)
        combined_matrix = scale_matrix * rot_matrix

        try:
            page.insert_text(
                insert_pt,
                text,
                fontsize=fontsize,
                fontname="helv",
                render_mode=render_mode,
                color=text_color,
                morph=(insert_pt, combined_matrix)
            )

            words_added += 1

        except Exception as e:
            log.debug(f"Could not insert '{text}': {e}")

    return words_added

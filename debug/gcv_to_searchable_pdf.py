#!/usr/bin/env python3
"""
Google Cloud Vision JSON to Searchable PDF - WITH ROTATION SUPPORT

Converts Google Cloud Vision OCR JSON output to a searchable PDF by adding
an invisible (or visible in debug mode) text layer over the original image.

Properly handles rotated text by:
1. Calculating rotation angles from quadrilateral bounding boxes
2. Scaling text horizontally to match bounding box width exactly

Usage:
    python gcv_to_searchable_pdf.py <image> <gcv_json> <output.pdf> [--debug]

Options:
    --debug    Show visible text overlay and bounding boxes for debugging

Requirements:
    pip install pymupdf
"""

import json
import sys
import math
import argparse
import fitz  # PyMuPDF


def get_text_geometry(vertices):
    """
    Calculate text geometry from Google Vision bounding box vertices.
    
    Google Vision provides vertices in reading order:
    [0] = start of text (top-left in reading direction)
    [1] = end of text (top-right in reading direction)
    [2] = bottom-right
    [3] = bottom-left
    
    v[0] -> v[1] = text direction (length)
    v[0] -> v[3] = perpendicular (height)
    
    Returns:
        dict with keys: angle, box_width, box_height, baseline_point
    """
    if len(vertices) < 4:
        return None
    
    # Vector along text direction (v0 to v1)
    dx_text = vertices[1].get('x', 0) - vertices[0].get('x', 0)
    dy_text = vertices[1].get('y', 0) - vertices[0].get('y', 0)
    box_width = math.sqrt(dx_text**2 + dy_text**2)
    
    # Vector perpendicular to text (v0 to v3) - character height direction
    dx_perp = vertices[3].get('x', 0) - vertices[0].get('x', 0)
    dy_perp = vertices[3].get('y', 0) - vertices[0].get('y', 0)
    box_height = math.sqrt(dx_perp**2 + dy_perp**2)
    
    # Angle of text direction in image coordinates
    image_angle = math.degrees(math.atan2(dy_text, dx_text))
    
    # Negate for PDF coordinate system
    pdf_angle = -image_angle
    
    # Calculate baseline insertion point
    # PDF text is positioned at baseline, offset from v[0] toward v[3]
    baseline_ratio = 0.75
    baseline_x = vertices[0].get('x', 0) + dx_perp * baseline_ratio
    baseline_y = vertices[0].get('y', 0) + dy_perp * baseline_ratio
    
    return {
        'angle': pdf_angle,
        'box_width': box_width,
        'box_height': box_height,
        'baseline_point': (baseline_x, baseline_y),
    }


def create_searchable_pdf(image_path, gcv_json_path, output_path, debug=False):
    """
    Create a searchable PDF from an image and Google Cloud Vision JSON output.
    
    Args:
        image_path: Path to the source image
        gcv_json_path: Path to Google Cloud Vision JSON output
        output_path: Path for the output PDF
        debug: If True, show visible text and bounding boxes
    
    Returns:
        tuple: (words_added, rotated_count, img_width, img_height)
    """
    # Load the GCV JSON
    with open(gcv_json_path, 'r') as f:
        gcv_data = json.load(f)
    
    # Get word-level annotations (skip first which is full text)
    responses = gcv_data.get('responses', [{}])
    text_annotations = responses[0].get('textAnnotations', [])
    words = text_annotations[1:] if len(text_annotations) > 1 else []
    
    # Create PDF document
    doc = fitz.open()
    
    # Get image dimensions and create page
    pix = fitz.Pixmap(image_path)
    img_width = pix.width
    img_height = pix.height
    pix = None  # Free memory
    
    # Create page with image dimensions
    page = doc.new_page(width=img_width, height=img_height)
    page.insert_image(page.rect, filename=image_path)
    
    # Font for text insertion
    font = fitz.Font("helv")
    
    words_added = 0
    rotated_count = 0
    
    for word_data in words:
        text = word_data.get('description', '')
        vertices = word_data.get('boundingPoly', {}).get('vertices', [])
        
        if len(vertices) < 4 or not text.strip():
            continue
        
        # Get text geometry
        geom = get_text_geometry(vertices)
        if geom is None:
            continue
        
        pdf_angle = geom['angle']
        box_width = geom['box_width']
        box_height = geom['box_height']
        insert_x, insert_y = geom['baseline_point']
        
        # Font size based on box HEIGHT
        fontsize = box_height * 0.75
        fontsize = max(fontsize, 6)
        
        # Calculate horizontal scale to match box width exactly
        natural_width = font.text_length(text, fontsize=fontsize)
        if natural_width > 0:
            h_scale = box_width / natural_width
        else:
            h_scale = 1.0
        
        # Debug mode: draw bounding box
        if debug:
            points = [(v.get('x', 0), v.get('y', 0)) for v in vertices]
            shape = page.new_shape()
            shape.draw_polyline(points + [points[0]])
            shape.finish(color=(1, 0, 0), width=1)  # Red outline
            shape.commit()
        
        # Build transformation matrix
        # Order: scale * rot (rotate first, then scale along text direction)
        insert_pt = fitz.Point(insert_x, insert_y)
        scale_matrix = fitz.Matrix(h_scale, 0, 0, 1, 0, 0)
        rot_matrix = fitz.Matrix(1, 0, 0, 1, 0, 0).prerotate(pdf_angle)
        combined_matrix = scale_matrix * rot_matrix
        
        # Insert text
        render_mode = 0 if debug else 3  # 0 = visible, 3 = invisible
        text_color = (0, 0, 1) if debug else None  # Blue for debug
        
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
            if abs(pdf_angle) > 5:
                rotated_count += 1
                
        except Exception as e:
            print(f"Warning: Could not insert '{text}': {e}", file=sys.stderr)
    
    # Save the PDF
    doc.save(output_path)
    doc.close()
    
    return words_added, rotated_count, img_width, img_height


def main():
    parser = argparse.ArgumentParser(
        description='Convert Google Cloud Vision OCR output to searchable PDF'
    )
    parser.add_argument('image', help='Path to source image')
    parser.add_argument('gcv_json', help='Path to Google Cloud Vision JSON output')
    parser.add_argument('output', help='Path for output PDF')
    parser.add_argument('--debug', action='store_true',
                        help='Show visible text overlay and bounding boxes')
    
    args = parser.parse_args()
    
    words, rotated, width, height = create_searchable_pdf(
        args.image,
        args.gcv_json,
        args.output,
        debug=args.debug
    )
    
    print(f"Created: {args.output}")
    print(f"  - Image dimensions: {width}x{height}")
    print(f"  - Words added: {words}")
    print(f"  - Rotated words: {rotated}")
    if args.debug:
        print(f"  - Debug mode: ON (visible text + bounding boxes)")


if __name__ == "__main__":
    main()

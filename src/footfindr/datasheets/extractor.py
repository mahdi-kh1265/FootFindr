"""Datasheet text extractor.

Extracts text from local PDF files.  Uses PyMuPDF (fitz) if available,
otherwise creates a placeholder extraction with metadata.

No network calls.  No AI calls from this module — that's in ai/.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from footfindr.datasheets.index import DatasheetIndex


def extract_text(pdf_path: str | Path) -> Optional[str]:
    """Extract text from a PDF file.

    Returns the extracted text, or None if PyMuPDF is not available.
    """
    try:
        import fitz  # PyMuPDF — optional dependency
    except ImportError:
        return None

    doc = fitz.open(str(pdf_path))
    pages: list[str] = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n\n".join(pages)


def extract_datasheet(
    mpn: str,
    index: DatasheetIndex,
    output_dir: Optional[str | Path] = None,
) -> tuple[Optional[Path], Optional[Path]]:
    """Extract text from a datasheet PDF and save it.

    Returns (text_path, json_metadata_path) or (None, None) if extraction
    fails or the PDF is not indexed.
    """
    record = index.get(mpn)
    if not record or not record.local_path:
        return None, None

    pdf_path = Path(record.local_path)
    if not pdf_path.exists():
        return None, None

    if output_dir is None:
        output_dir = pdf_path.parent
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_mpn = mpn.replace("/", "_").replace(" ", "_")

    text = extract_text(pdf_path)
    text_path = output_dir / f"{safe_mpn}_extracted.txt"
    json_path = output_dir / f"{safe_mpn}_metadata.json"

    if text:
        text_path.write_text(text, encoding="utf-8")
    else:
        # PyMuPDF not available — create placeholder
        text_path.write_text(
            f"# Datasheet text extraction placeholder for {mpn}\n"
            f"# Install PyMuPDF (`pip install pymupdf`) for actual extraction.\n"
            f"# Source: {pdf_path}\n",
            encoding="utf-8",
        )

    # Write metadata JSON
    metadata = {
        "mpn": mpn,
        "source_pdf": str(pdf_path),
        "extracted": text is not None,
        "text_path": str(text_path),
        "page_count": _get_page_count(pdf_path),
    }
    json_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    index.update_extracted(mpn, str(text_path), str(json_path))
    return text_path, json_path


def _get_page_count(pdf_path: Path) -> Optional[int]:
    """Get page count from a PDF, or None if PyMuPDF is unavailable."""
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        count = len(doc)
        doc.close()
        return count
    except ImportError:
        return None

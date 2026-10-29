"""Convert an uploaded document into cleaned PNG pages for the model."""
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageSequence


def _save(img: Image.Image, path: Path, max_edge: int) -> Path:
    img = img.convert("RGB")
    img.thumbnail((max_edge, max_edge))
    img.save(path, "PNG")
    return path


def to_pages(doc_path, out_dir, dpi=300, max_edge=1600) -> list[Path]:
    """Render each page to PNG. Raise max_edge for handwritten instruments."""
    doc_path, out_dir = Path(doc_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    if doc_path.suffix.lower() == ".pdf":
        pdf = pdfium.PdfDocument(str(doc_path))
        try:
            for i in range(len(pdf)):
                img = pdf[i].render(scale=dpi / 72).to_pil()
                pages.append(_save(img, out_dir / f"p{i + 1:03d}.png", max_edge))
        finally:
            pdf.close()
    else:
        with Image.open(doc_path) as im:
            for i, frame in enumerate(ImageSequence.Iterator(im), 1):
                pages.append(_save(frame, out_dir / f"p{i:03d}.png", max_edge))
    # TODO: deskew and border cropping once real scans are available.
    return pages

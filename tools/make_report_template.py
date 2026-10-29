"""Make the blank surface title report template from a completed SEG report.

    python tools/make_report_template.py "CCS.TX.SDY.0143_UPDATED_07032025.xlsx"

Keeps every sheet's layout, column widths, fonts, borders, merges, print setup and boilerplate text;
clears the tract's own data. Writes src/titlepilot/templates/surface_title_report.xlsx.

What it can't keep: the Flowchart sheet's drawn boxes and arrows, and the Runsheet's header logo
(the spreadsheet library drops drawings). The report writer draws its own flowchart in cells.
"""
import sys
from pathlib import Path

import openpyxl

OUT = Path(__file__).resolve().parents[1] / "src" / "titlepilot" / "templates" / "surface_title_report.xlsx"

# Cells holding the tract's own data (labels and boilerplate are left alone).
CLEAR = {
    "Ownership ": ["B8", "B9", "B11", "C14", "E14", "C15", "C16", "E16", "C17", "E17", "C18", "B22",
                   "B28", "C28", "D28", "E28", "B35", "C35", "D35", "E35", "B62"],
    "Encumbrances": ["C6", "C18", "B24", "B29", "B34", "B39", "B44"],
}
DATA_ROWS = {                          # sheet: (first data row, first col, last col)
    "Runsheet ": (13, 1, 11),
    "Index to All Documents": (3, 2, 9),
    "Names Examined": (5, 2, 13),
    "Ownership BT Data": (2, 1, 12),
}
RUNSHEET_HEADER_CELLS = ["A4", "A6", "A8"]


def main(src):
    wb = openpyxl.load_workbook(src)
    for sheet, cells in CLEAR.items():
        for a in cells:
            wb[sheet][a].value = None
    for a in RUNSHEET_HEADER_CELLS:
        wb["Runsheet "][a].value = None
    for sheet, (first, c1, c2) in DATA_ROWS.items():
        ws = wb[sheet]
        for r in range(first, ws.max_row + 1):
            for c in range(c1, c2 + 1):
                ws.cell(r, c).value = None
    # Row-specific highlight rules from the source tract don't apply to other tracts.
    for sheet in ("Runsheet ", "Index to All Documents"):
        wb[sheet].conditional_formatting._cf_rules.clear()
    # The header logo is dropped on save; don't leave a header that points at a missing picture.
    wb["Runsheet "].oddHeader.center.text = None
    # Flowchart: the drawn shapes are lost anyway; clear stray cell values and the print area.
    fc = wb["Flowchart"]
    for row in fc.iter_rows():
        for c in row:
            c.value = None
    fc.print_area = None
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"Template written to {OUT}")


if __name__ == "__main__":
    main(sys.argv[1])

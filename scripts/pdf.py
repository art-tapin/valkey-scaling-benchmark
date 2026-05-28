#!/usr/bin/env python3
"""Generate PDF from benchmark HTML report."""

import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright


def generate_pdf(
    html_path: Path,
    output_path: Path,
    scale: float = 1.0,
) -> None:
    """Convert HTML report to PDF."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        # Load the HTML file
        page.goto(f"file://{html_path.absolute()}")

        # Wait for Plotly charts to render
        page.wait_for_timeout(2000)

        # Generate PDF
        page.pdf(
            path=str(output_path),
            scale=scale,
            format="A4",
            print_background=True,
            margin={"top": "20px", "bottom": "20px", "left": "20px", "right": "20px"},
        )

        browser.close()

    print(f"PDF saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate PDF from HTML report")
    parser.add_argument(
        "--scale",
        type=float,
        default=0.8,
        help="Scale factor (default: 0.8)",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).parent.parent / "results" / "report.html",
        help="Input HTML file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output PDF file (default: same as input with .pdf)",
    )
    args = parser.parse_args()

    output = args.output or args.input.with_suffix(".pdf")

    generate_pdf(args.input, output, args.scale)


if __name__ == "__main__":
    main()

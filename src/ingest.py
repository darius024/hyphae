"""
Corpus ingestion CLI for Hyphae.

Manages the local research document corpus -- add PDFs, text files, or
entire directories; list indexed documents; remove documents.

All data stays local. Extracted text is stored in corpus/ for Cactus RAG
to index automatically.

Usage:
    python ingest.py add paper.pdf
    python ingest.py add notes/
    python ingest.py list
    python ingest.py remove <filename>
"""

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

from config import CORPUS_DIR

log = logging.getLogger(__name__)


def extract_pdf_text(pdf_path: str) -> str:
    """Extract text from a PDF using PyMuPDF."""
    try:
        import fitz
    except ImportError:
        log.error("pymupdf is required for PDF ingestion. Install with: pip install pymupdf")
        raise ImportError("pymupdf is required for PDF ingestion")

    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        text = page.get_text()
        if text.strip():
            pages.append(text)
    doc.close()
    return "\n\n".join(pages)


def add_file(filepath: str, dest_name: str = None) -> bool:
    """Add a single file to the corpus. Returns True on success."""
    path = Path(filepath)
    if not path.exists():
        print(f"  SKIP: {filepath} does not exist")
        return False

    os.makedirs(CORPUS_DIR, exist_ok=True)
    if dest_name is None:
        dest_name = path.stem + ".txt"
    dest = Path(CORPUS_DIR) / dest_name

    if path.suffix.lower() == ".pdf":
        text = extract_pdf_text(str(path))
        if not text.strip():
            print(f"  SKIP: {filepath} -- no extractable text")
            return False
        with open(dest, "w", encoding="utf-8") as f:
            f.write(f"# Source: {path.name}\n\n")
            f.write(text)
        print(f"  ADD:  {path.name} -> {dest} ({len(text)} chars)")
    elif path.suffix.lower() in (".txt", ".md", ".csv", ".log", ".json"):
        if dest.exists() and dest.resolve() == path.resolve():
            print(f"  SKIP: {filepath} is already in corpus")
            return False
        shutil.copy2(str(path), str(dest))
        print(f"  ADD:  {path.name} -> {dest}")
    else:
        print(f"  SKIP: {filepath} -- unsupported format ({path.suffix})")
        return False

    return True


def add_directory(dirpath: str) -> int:
    """Recursively add all supported files from a directory. Returns count added."""
    count = 0
    supported = {".pdf", ".txt", ".md", ".csv", ".log", ".json"}
    for root, _, files in os.walk(dirpath):
        for name in sorted(files):
            if Path(name).suffix.lower() in supported:
                if add_file(os.path.join(root, name)):
                    count += 1
    return count


def list_documents():
    """List all documents in the corpus."""
    corpus = Path(CORPUS_DIR)
    if not corpus.is_dir():
        print("Corpus is empty (directory does not exist).")
        return

    docs = sorted(corpus.iterdir())
    docs = [d for d in docs if d.is_file() and not d.name.startswith(".")]

    if not docs:
        print("Corpus is empty.")
        return

    print(f"\n{'Name':<40} {'Size':>10}")
    print("-" * 52)
    total_size = 0
    for doc in docs:
        size = doc.stat().st_size
        total_size += size
        size_str = f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"
        print(f"  {doc.name:<38} {size_str:>10}")

    print("-" * 52)
    print(f"  {len(docs)} documents, {total_size / 1024:.1f} KB total\n")


def remove_document(name: str):
    """Remove a document from the corpus by filename."""
    path = Path(CORPUS_DIR) / name
    if not path.exists():
        path_txt = Path(CORPUS_DIR) / (name + ".txt")
        if path_txt.exists():
            path = path_txt
        else:
            print(f"Not found: {name}")
            return

    path.unlink()
    print(f"Removed: {path}")


def main():
    parser = argparse.ArgumentParser(
        description="Hyphae corpus ingestion tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python ingest.py add paper.pdf\n"
               "  python ingest.py add research_data/\n"
               "  python ingest.py list\n"
               "  python ingest.py remove old_notes.txt\n",
    )
    sub = parser.add_subparsers(dest="command")

    add_parser = sub.add_parser("add", help="Add a file or directory to the corpus")
    add_parser.add_argument("path", help="File or directory to add")

    sub.add_parser("list", help="List all documents in the corpus")

    rm_parser = sub.add_parser("remove", help="Remove a document from the corpus")
    rm_parser.add_argument("name", help="Filename to remove")

    args = parser.parse_args()

    if args.command == "add":
        target = Path(args.path)
        if target.is_dir():
            count = add_directory(str(target))
            print(f"\nAdded {count} document(s) to corpus.")
        elif target.is_file():
            add_file(str(target))
        else:
            print(f"Not found: {args.path}")
            sys.exit(1)
    elif args.command == "list":
        list_documents()
    elif args.command == "remove":
        remove_document(args.name)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

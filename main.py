"""
LPC OCR Extractor — Main entry point

Backends:
  gemini  → Gemini 2.0 Flash (free API, PDF native, best accuracy)
  ollama  → Qwen2.5-VL local (offline, private, M4 16GB compatible)

Usage:
    python main.py --pdfs file1.pdf file2.pdf
    python main.py --folder ./scans/ --backend gemini
    python main.py --folder ./scans/ --backend ollama --model qwen2.5vl
    python main.py --pdfs *.pdf --output reports/LPC_Jan.xlsx
"""
import argparse
import os
import pathlib
import sys

from dotenv import load_dotenv

load_dotenv()


def collect_pdfs(args) -> list[pathlib.Path]:
    pdfs = []
    if args.pdfs:
        for p in args.pdfs:
            path = pathlib.Path(p)
            if not path.exists():
                print(f"WARNING: Not found: {p}")
            else:
                pdfs.append(path)
    if args.folder:
        folder = pathlib.Path(args.folder)
        pdfs += sorted(folder.glob("*.pdf")) + sorted(folder.glob("*.PDF"))
    return pdfs


def get_extractor(backend: str, model: str = None):
    if backend == "gemini":
        from extractor.gemini_extractor import GeminiLPCExtractor
        return GeminiLPCExtractor()
    elif backend == "groq":
        from extractor.groq_extractor import GroqLPCExtractor
        return GroqLPCExtractor(model=model)
    elif backend == "openrouter":
        from extractor.openrouter_extractor import OpenRouterLPCExtractor
        return OpenRouterLPCExtractor(model=model)
    elif backend == "ollama":
        from extractor.ollama_extractor import OllamaLPCExtractor
        return OllamaLPCExtractor(model=model or "qwen2.5vl")
    else:
        raise ValueError(f"Unknown backend: {backend}. Use 'gemini', 'groq', or 'ollama'")


def main():
    parser = argparse.ArgumentParser(description="LPC OCR → Excel (4 sheets)")
    parser.add_argument("--pdfs",    nargs="+", help="PDF file paths")
    parser.add_argument("--folder",  help="Folder of PDFs")
    parser.add_argument("--output",  default=None, help="Output .xlsx path")
    parser.add_argument("--backend", default=os.getenv("MODEL_BACKEND", "gemini"),
                        choices=["gemini", "groq", "openrouter", "ollama"],
                        help="Model backend (default: gemini)")
    parser.add_argument("--model",   default=None,
                        help="Ollama model name (default: qwen2.5vl)")
    parser.add_argument("--json",    action="store_true",
                        help="Save raw JSON per PDF in output/")
    parser.add_argument("--ping",   action="store_true",
                        help="Test API key + quota with tiny request, then exit")
    args = parser.parse_args()

    # Quick API connectivity test
    if args.ping:
        from google import genai
        from google.genai import types
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            print("ERROR: GEMINI_API_KEY not set in .env")
            sys.exit(1)
        print("Pinging Gemini API ...")
        try:
            client = genai.Client(api_key=key)
            model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
            r = client.models.generate_content(
                model=model_name,
                contents=["Reply with just: OK"],
                config=types.GenerateContentConfig(temperature=0),
            )
            print(f"✓ API working | model={model_name} | response={r.text.strip()}")
        except Exception as e:
            print(f"✗ API error: {e}")
        sys.exit(0)

    pdfs = collect_pdfs(args)
    if not pdfs:
        print("ERROR: No PDFs found. Use --pdfs or --folder.")
        sys.exit(1)

    print(f"\nLPC Extractor [{args.backend.upper()}] — {len(pdfs)} PDF(s)")
    print("=" * 55)

    from extractor.excel_writer import ExcelWriter
    from models.lpc import LPCRecord

    extractor = get_extractor(args.backend, args.model)
    writer    = ExcelWriter()
    ok, fail  = [], []

    for i, pdf in enumerate(pdfs, 1):
        print(f"\n[{i}/{len(pdfs)}] {pdf.name}")
        try:
            record = extractor.extract_pdf(str(pdf))
            writer.add_record(record)
            ok.append(record)
            print(f"  ✓ stage={record.stage} | student={record.student_name or '?'} | months={len(record.months)}")

            if args.json:
                jp = pathlib.Path("output") / f"{pdf.stem}.json"
                jp.parent.mkdir(exist_ok=True)
                jp.write_text(record.model_dump_json(indent=2))
                print(f"  JSON → {jp}")

        except Exception as e:
            print(f"  ✗ ERROR: {e}")
            fail.append((pdf.name, str(e)))

    print("\n" + "=" * 55)
    print(f"Success: {len(ok)}/{len(pdfs)}")

    if ok:
        out = writer.save(args.output)
        print(f"Excel  → {out}")
        print(f"Sheets → {len(set(r.stage for r in ok))} stage(s) populated")

    if fail:
        print(f"\nFailed ({len(fail)}):")
        for name, err in fail:
            print(f"  {name}: {err}")

    print("\nDone.")


if __name__ == "__main__":
    main()

"""
Hyphae Web API — Flask backend for the research copilot.

Endpoints:
    POST /api/query       — run a natural-language query through hybrid routing
    GET  /api/documents   — list corpus documents
    POST /api/upload      — upload PDF / text files to corpus
    POST /api/voice       — upload audio, transcribe, then query
    DELETE /api/documents/<name> — remove a document from corpus

Usage:
    python app.py                     # default (port 5000)
    PORT=8080 python app.py           # custom port
    CLOUD_ONLY=1 python app.py        # skip local inference
"""

import sys, os, time, json, tempfile, logging
from pathlib import Path

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)
sys.path.insert(0, os.path.join(_project_root, "src"))

from flask import Flask, request, jsonify, send_from_directory
from google import genai

from main import generate_hybrid
from tools import ALL_TOOLS, execute_tool
from ingest import add_file, list_documents as list_corpus, remove_document, extract_pdf_text
from config import CORPUS_DIR

log = logging.getLogger(__name__)

_static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app = Flask(__name__, static_folder=_static_dir, static_url_path="")


def synthesise_answer(user_message, tool_results):
    """Generate a natural language answer from tool results via Gemini."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not tool_results:
        return None

    results_text = ""
    for tr in tool_results:
        result_data = tr["result"]
        if "error" in result_data:
            results_text += f"\nTool {tr['tool']} failed: {result_data['error']}\n"
            continue

        if tr["tool"] == "search_papers":
            chunks = result_data.get("results", [])
            results_text += f"\n[search_papers found {len(chunks)} passages]\n"
            for c in chunks[:5]:
                results_text += f"- {c.get('text', '')[:300]}\n"

        elif tr["tool"] == "summarise_notes":
            results_text += f"\n[summary]\n{result_data.get('summary', '')}\n"

        elif tr["tool"] == "create_note":
            results_text += f"\n[note saved to {result_data.get('saved', '')}]\n"

        elif tr["tool"] == "list_documents":
            docs = result_data.get("documents", [])
            results_text += f"\n[{len(docs)} documents in corpus]\n"
            for d in docs:
                results_text += f"- {d['name']} ({d.get('size_kb', '?')} KB)\n"

        elif tr["tool"] == "generate_hypothesis":
            results_text += f"\n[hypotheses]\n{result_data.get('hypotheses', '')}\n"

        elif tr["tool"] == "search_literature":
            results_text += f"\n[literature]\n{result_data.get('results', '')}\n"

        elif tr["tool"] == "compare_documents":
            results_text += f"\n[comparison]\n{result_data.get('comparison', '')}\n"

        else:
            results_text += f"\n[{tr['tool']}]\n{json.dumps(result_data, indent=2)[:500]}\n"

    prompt = (
        f"The user asked: \"{user_message}\"\n\n"
        f"The system executed tools and got these results:\n{results_text}\n\n"
        "Based on these results, write a helpful, concise answer to the user's question. "
        "Reference specific data from the results. Do not mention tool names or internal details. "
        "Write as a knowledgeable research assistant."
    )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt],
        )
        return response.text
    except Exception as e:
        log.warning("Response synthesis failed: %s", e)
        return None


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/query", methods=["POST"])
def api_query():
    """Run a query through hybrid routing and execute any tool calls."""
    data = request.get_json(force=True)
    user_message = data.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "message is required"}), 400

    messages = [{"role": "user", "content": user_message}]
    tools = data.get("tools", [t for t in ALL_TOOLS])

    start = time.time()
    result = generate_hybrid(messages, tools)
    routing_ms = (time.time() - start) * 1000

    tool_results = []
    for fc in result.get("function_calls", []):
        tr = execute_tool(fc["name"], fc.get("arguments", {}))
        tool_results.append({"tool": fc["name"], "arguments": fc.get("arguments", {}), "result": tr})

    answer = synthesise_answer(user_message, tool_results)

    return jsonify({
        "source": result.get("source", "unknown"),
        "routing_ms": round(routing_ms, 1),
        "function_calls": result.get("function_calls", []),
        "tool_results": tool_results,
        "answer": answer,
        "confidence": result.get("confidence", None),
    })


@app.route("/api/documents", methods=["GET"])
def api_documents():
    """List all documents in the corpus."""
    corpus = Path(CORPUS_DIR)
    if not corpus.is_dir():
        return jsonify({"documents": [], "count": 0})

    docs = []
    for f in sorted(corpus.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            docs.append({
                "name": f.name,
                "size_kb": round(f.stat().st_size / 1024, 1),
            })

    return jsonify({"documents": docs, "count": len(docs)})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """Upload files to the corpus."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    uploaded = request.files.getlist("file")
    results = []

    for f in uploaded:
        if not f.filename:
            continue

        original_name = Path(f.filename).name
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(f.filename).suffix) as tmp:
            f.save(tmp.name)
            tmp_path = tmp.name

        try:
            success = add_file(tmp_path, dest_name=Path(original_name).stem + ".txt")
            results.append({"filename": f.filename, "added": bool(success)})
        finally:
            os.unlink(tmp_path)

    return jsonify({"uploaded": results})


@app.route("/api/documents/<name>", methods=["DELETE"])
def api_remove_document(name):
    """Remove a document from the corpus."""
    path = Path(CORPUS_DIR) / name
    if not path.exists():
        return jsonify({"error": f"Not found: {name}"}), 404

    path.unlink()
    return jsonify({"removed": name})


def _convert_to_wav(input_path):
    """Convert any audio file to 16kHz mono WAV via ffmpeg. Returns WAV path."""
    if input_path.endswith(".wav"):
        return input_path
    wav_path = input_path.rsplit(".", 1)[0] + ".wav"
    try:
        import subprocess
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", wav_path],
            check=True, capture_output=True,
        )
        return wav_path
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        log.warning("ffmpeg conversion failed: %s", e)
        return input_path


@app.route("/api/voice", methods=["POST"])
def api_voice():
    """Accept audio upload, transcribe with Whisper, then run query."""
    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio = request.files["audio"]
    suffix = Path(audio.filename).suffix if audio.filename else ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        audio.save(tmp.name)
        tmp_path = tmp.name

    wav_path = _convert_to_wav(tmp_path)
    cleanup = [tmp_path]
    if wav_path != tmp_path:
        cleanup.append(wav_path)

    try:
        from voice import transcribe_file
        transcript = transcribe_file(wav_path)
    except Exception as e:
        return jsonify({"error": f"Transcription failed: {e}"}), 500
    finally:
        for p in cleanup:
            try:
                os.unlink(p)
            except OSError:
                pass

    if not transcript.strip():
        return jsonify({"error": "Could not transcribe audio"}), 400

    messages = [{"role": "user", "content": transcript}]
    tools = [t for t in ALL_TOOLS]

    start = time.time()
    result = generate_hybrid(messages, tools)
    routing_ms = (time.time() - start) * 1000

    tool_results = []
    for fc in result.get("function_calls", []):
        tr = execute_tool(fc["name"], fc.get("arguments", {}))
        tool_results.append({"tool": fc["name"], "arguments": fc.get("arguments", {}), "result": tr})

    answer = synthesise_answer(transcript, tool_results)

    return jsonify({
        "transcript": transcript,
        "source": result.get("source", "unknown"),
        "routing_ms": round(routing_ms, 1),
        "function_calls": result.get("function_calls", []),
        "tool_results": tool_results,
        "answer": answer,
    })


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    log.info("Hyphae server starting on http://localhost:%d", port)
    app.run(host="0.0.0.0", port=port, debug=debug)

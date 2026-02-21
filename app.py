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

import sys, os, time, json, tempfile
from pathlib import Path

sys.path.insert(0, "cactus/python/src")

from flask import Flask, request, jsonify, send_from_directory

from main import generate_hybrid
from tools import ALL_TOOLS, execute_tool
from ingest import add_file, list_documents as list_corpus, remove_document, extract_pdf_text, CORPUS_DIR

app = Flask(__name__, static_folder="static", static_url_path="")


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

    return jsonify({
        "source": result.get("source", "unknown"),
        "routing_ms": round(routing_ms, 1),
        "function_calls": result.get("function_calls", []),
        "tool_results": tool_results,
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


@app.route("/api/voice", methods=["POST"])
def api_voice():
    """Accept audio upload, transcribe with Whisper, then run query."""
    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio = request.files["audio"]
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        audio.save(tmp.name)
        tmp_path = tmp.name

    try:
        from voice import transcribe_file
        transcript = transcribe_file(tmp_path)
    except Exception as e:
        return jsonify({"error": f"Transcription failed: {e}"}), 500
    finally:
        os.unlink(tmp_path)

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

    return jsonify({
        "transcript": transcript,
        "source": result.get("source", "unknown"),
        "routing_ms": round(routing_ms, 1),
        "function_calls": result.get("function_calls", []),
        "tool_results": tool_results,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    print(f"Hyphae server starting on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)

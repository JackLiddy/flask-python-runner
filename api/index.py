import io
import os
import base64
import contextlib
import traceback

import matplotlib
matplotlib.use('Agg')  # Must be before any other matplotlib import
import matplotlib.pyplot as plt

from flask import Flask, request, jsonify

app = Flask(__name__)

# Save the real savefig/show once at import time
_original_savefig = plt.savefig
_original_show = plt.show


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capture_savefig(saved_plots: dict):
    """
    Return a patched plt.savefig that captures figures to an in-memory dict
    instead of writing to disk. The key is the stringified fname argument so
    the calling code's filename logic is preserved.
    """
    def patched_savefig(fname, *args, **kwargs):
        buf = io.BytesIO()
        kwargs.pop('format', None)          # force PNG
        kwargs.pop('bbox_inches', None)
        _original_savefig(buf, *args, format='png', bbox_inches='tight', **kwargs)
        plt.close('all')
        key = str(fname)
        saved_plots[key] = (
            "data:image/png;base64,"
            + base64.b64encode(buf.getvalue()).decode()
        )

    return patched_savefig


def _save_uploads_to_tmp(files) -> dict:
    """Write uploaded files to /tmp and return {original_filename: tmp_path}."""
    saved = {}
    for name, file_obj in files.items(multi=True):
        tmp_path = os.path.join('/tmp', file_obj.filename)
        file_obj.save(tmp_path)
        saved[file_obj.filename] = tmp_path
    return saved


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@app.route('/api/execute', methods=['POST'])
def execute():
    """
    Accepts multipart/form-data:
      - code  (required): the Python code string to execute
      - files (optional, multiple): any data files the code references

    Returns JSON:
      {
        "stdout": "...",
        "stderr": "...",
        "plots":  { "overview.png": "data:image/png;base64,...", ... }
      }
    """

    # --- Parse request -------------------------------------------------------
    code = request.form.get('code', '')
    if not code:
        return jsonify({"error": "No code provided"}), 400

    # Save any uploaded files to /tmp so the code can open them by name
    if request.files:
        _save_uploads_to_tmp(request.files)

    # --- Patch the execution environment ------------------------------------
    saved_plots = {}

    # Override savefig globally before exec so every plt.savefig call is caught
    plt.savefig = _capture_savefig(saved_plots)
    plt.show    = lambda: None          # no-op — no display in serverless

    # Inject os.chdir to /tmp so relative file opens work without code changes
    os.chdir('/tmp')

    # --- Execute -------------------------------------------------------------
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    with contextlib.redirect_stdout(stdout_buf), \
         contextlib.redirect_stderr(stderr_buf):
        try:
            exec(code, {"__builtins__": __builtins__})
        except Exception:
            stderr_buf.write(traceback.format_exc())

    # Restore working directory (good hygiene between requests)
    os.chdir('/')

    return jsonify({
        "stdout": stdout_buf.getvalue(),
        "stderr": stderr_buf.getvalue(),
        "plots":  saved_plots,
    })

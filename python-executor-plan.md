# Python Executor API — Vercel Deployment Plan

A self-contained Flask API that accepts file uploads + a code string, executes
the code in a patched environment, and returns stdout/stderr plus all generated
plots as base64 PNGs.

---

## ⚠️ Package Size Warning (Read First)

Vercel's limit for a serverless Python function is **250 MB unzipped**. The
dependency stack required for this code is large:

| Package | Approx. size |
|---|---|
| numpy | ~30 MB |
| scipy | ~50 MB |
| matplotlib | ~30 MB |
| pynwb + h5py + hdmf | ~40 MB |
| nd2reader | ~5 MB |
| tifffile | ~5 MB |
| **Total** | **~160 MB** |

This is close but should fit. If Vercel rejects the deploy due to size, the
fallback plan is Railway (same code, zero changes, no size limit).

---

## Project Structure

```
python-executor/
├── api/
│   └── index.py          # Flask app — Vercel auto-detects this
├── requirements.txt
├── vercel.json
└── README.md
```

---

## `requirements.txt`

```
flask
numpy
scipy
matplotlib
tifffile
nd2reader
pynwb
```

> **Note:** Do NOT pin versions unless you hit conflicts. Let Vercel resolve
> the latest compatible set on first deploy. If `nd2reader` causes issues
> (it can be fussy), try `nd2` as an alternative drop-in.

---

## `vercel.json`

```json
{
  "rewrites": [
    { "source": "/api/(.*)", "destination": "/api/index.py" }
  ]
}
```

---

## `api/index.py` — Full Flask App

```python
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capture_savefig(saved_plots: dict):
    """
    Return a patched plt.savefig that captures figures to an in-memory dict
    instead of writing to disk. The key is the stringified fname argument so
    the calling code's filename logic is preserved.
    """
    original = plt.savefig

    def patched_savefig(fname, *args, **kwargs):
        buf = io.BytesIO()
        kwargs.pop('format', None)          # force PNG
        original(buf, *args, format='png', bbox_inches='tight', **kwargs)
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
    for name, file_obj in files.items():
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
```

---

## Key Design Decisions

### 1. `os.chdir('/tmp')` — No Code Modifications Required

The user's code does `open('20191010_tail_01.nd2')` — a relative path. By
`chdir`-ing to `/tmp` before `exec`, and having already saved uploads there,
relative file opens just work with zero changes to the user's code.

### 2. Patching `plt.savefig` Globally

The code calls `plt.savefig("overview.png")` and
`plt.savefig(f"{file_identifier}_comparison.png")`. Rather than intercepting
at the `exec` namespace level, patching `plt.savefig` globally before the
`exec` call catches all of them and stores them in `saved_plots` keyed by their
original filename strings.

### 3. `plt.show()` → no-op

Calling `plt.show()` in a headless server would hang or error. The lambda
replacement swallows it cleanly.

### 4. Full `__builtins__`

This code needs `open`, `os`, `print`, and more. Don't strip builtins here —
that's a security call you can tighten later once the functionality works.

---

## Necessary Code Modifications

The user's code is almost entirely runnable as-is. One change is needed and one
is recommended:

### Required: Remove or stub the file-existence check pattern

The code has `import os.path` but doesn't actually call `os.path.exists()` —
it just checks filename extensions. So `import os.path` is fine and needs no
changes.

### Recommended: Update how files are referenced in the files list

The code currently has:
```python
files = ['20191010_tail_01.nd2', '20240523_Vang-1_37.tif', 'sub-11-YAaLR_ophys.nwb']
```

This works fine with the `chdir('/tmp')` approach. Users uploading different
files just need to update this list to match their uploaded filenames. No other
changes needed.

### No changes needed for:
- `import os.path` ✅
- `np`, `scipy`, `matplotlib` ✅
- `plt.savefig(output_path)` ✅ (intercepted)
- `plt.show()` ✅ (no-op'd)
- File format branching logic ✅

---

## Deployment Steps

```bash
# 1. Create the repo
mkdir python-executor && cd python-executor
git init

# 2. Create the files above (api/index.py, requirements.txt, vercel.json)
mkdir api

# 3. Push to GitHub
git add . && git commit -m "init"
gh repo create python-executor --public --push
# (or use the GitHub UI)

# 4. Deploy on Vercel
# - Go to vercel.com → New Project → Import your repo
# - Framework Preset: Other
# - No build command needed
# - Deploy

# 5. Your endpoint is live at:
# https://your-project.vercel.app/api/execute
```

---

## Next.js Integration (Your Web App Side)

```typescript
// utils/runPython.ts

export async function runPython(code: string, files: File[] = []) {
  const formData = new FormData();
  formData.append('code', code);
  files.forEach(f => formData.append('files', f, f.name));

  const res = await fetch('https://your-project.vercel.app/api/execute', {
    method: 'POST',
    body: formData,
    // Do NOT set Content-Type — browser sets it with boundary automatically
  });

  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json() as Promise<{
    stdout: string;
    stderr: string;
    plots: Record<string, string>; // filename → data:image/png;base64,...
  }>;
}
```

```tsx
// In your component
const { stdout, stderr, plots } = await runPython(userCode, uploadedFiles);

// Render plots
Object.entries(plots).map(([name, dataUrl]) => (
  <img key={name} src={dataUrl} alt={name} />
))
```

---

## Fallback: Railway (If Vercel Size Limit Hit)

If the deploy fails due to package size, the **exact same `api/index.py` and
`requirements.txt`** work on Railway with one change: delete `vercel.json` and
add a `Procfile`:

```
web: gunicorn api.index:app
```

Add `gunicorn` to `requirements.txt`. Push to GitHub, connect to Railway, done.
The fetch URL in your Next.js app is the only thing that changes.

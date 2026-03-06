# Python Executor API — Integration Guide

## Endpoint

`POST /api/execute` (multipart/form-data)

## Request

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | string (form field) | Yes | Python code to execute |
| files | file uploads (any field name) | No | Data files the code references (e.g. `.nd2`, `.tif`, `.nwb`) |

**Important:** Use `multipart/form-data` encoding. Do NOT set the `Content-Type` header manually — let the browser/fetch set it automatically so the multipart boundary is included.

## Response

```json
{
  "stdout": "string — captured print() output",
  "stderr": "string — captured stderr and tracebacks (empty string if no errors)",
  "plots": {
    "filename.png": "data:image/png;base64,iVBORw0KGgo...",
    "another_plot.png": "data:image/png;base64,..."
  }
}
```

- `plots` is a key-value object where keys are the filenames passed to `plt.savefig()` and values are complete data URLs ready to use as `<img src>`.
- If the code has no `plt.savefig()` calls, `plots` will be `{}`.
- If the code throws an exception, `stderr` contains the full traceback. `stdout` still contains any output printed before the error.

### Error response (400)

```json
{ "error": "No code provided" }
```

Returned when the `code` form field is missing or empty.

## Environment Details

The executed Python code runs with:

- **Working directory:** `/tmp` — uploaded files are saved here, so relative paths like `open('data.tif')` just work
- **Available packages:** numpy, scipy, matplotlib, tifffile, nd2reader, pynwb (and their transitive deps like h5py, hdmf)
- **`plt.savefig()`** is intercepted — instead of writing to disk, it captures the figure as a base64 PNG and includes it in the response under `plots`
- **`plt.show()`** is a no-op — it won't error or hang
- **Full `__builtins__`** are available — `open()`, `print()`, `os`, `range`, etc. all work

## TypeScript Integration

### Utility function

```typescript
interface PythonResult {
  stdout: string;
  stderr: string;
  plots: Record<string, string>; // filename → "data:image/png;base64,..."
}

export async function runPython(
  code: string,
  files: File[] = []
): Promise<PythonResult> {
  const formData = new FormData();
  formData.append('code', code);
  files.forEach(f => formData.append('files', f, f.name));

  const res = await fetch(
    process.env.NEXT_PUBLIC_PYTHON_EXECUTOR_URL + '/api/execute',
    { method: 'POST', body: formData }
  );

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `API error: ${res.status}`);
  }

  return res.json();
}
```

### Rendering plots

Each value in `plots` is a complete `data:image/png;base64,...` data URL. Use it directly as an img src:

```tsx
const { stdout, stderr, plots } = await runPython(code, uploadedFiles);

// Render all generated plots
{Object.entries(plots).map(([name, dataUrl]) => (
  <img key={name} src={dataUrl} alt={name} />
))}
```

### Checking for errors

```typescript
const result = await runPython(code, files);

if (result.stderr) {
  // Code threw an exception or wrote to stderr
  console.error(result.stderr);
}
```

## Example: Minimal end-to-end call

```typescript
const result = await runPython(`
import numpy as np
import matplotlib.pyplot as plt

x = np.linspace(0, 10, 100)
plt.plot(x, np.sin(x))
plt.title("Sine Wave")
plt.savefig("sine.png")
print("Done!")
`);

// result.stdout  → "Done!\n"
// result.stderr  → ""
// result.plots   → { "sine.png": "data:image/png;base64,iVBORw0KGgo..." }
```

## Example: With file uploads

```typescript
// User uploads a .tif file via an <input type="file">
const file = inputElement.files[0]; // e.g. "sample.tif"

const result = await runPython(`
import tifffile
import matplotlib.pyplot as plt

img = tifffile.imread("sample.tif")
print(f"Shape: {img.shape}, dtype: {img.dtype}")
plt.imshow(img, cmap='gray')
plt.savefig("preview.png")
`, [file]);
```

The uploaded file is saved to `/tmp/sample.tif` and the code's working directory is `/tmp`, so `tifffile.imread("sample.tif")` resolves correctly.

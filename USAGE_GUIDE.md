# Python Executor API — Integration Guide

## Endpoint

`POST /api/execute` (multipart/form-data)

The API URL is configured via the environment variable `NEXT_PUBLIC_PYTHON_EXECUTOR_URL`.

- **Local development:** `http://127.0.0.1:5555`
- **Production (Vercel):** your Vercel deployment URL

Set this in your Next.js `.env.local`:

```
NEXT_PUBLIC_PYTHON_EXECUTOR_URL=http://127.0.0.1:5555
```

## Request

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | string (form field) | Yes | Python code to execute |
| files | file uploads (field name `files`) | No | Data files the code references (e.g. `.nd2`, `.tif`, `.nwb`) |

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

## Bundled Data Files

The server ships with three microscopy data files that are pre-loaded into the execution environment at startup. User code can reference these files by name without uploading them:

| Filename | Format | Size | Description |
|----------|--------|------|-------------|
| `20191010_tail_01.nd2` | Nikon ND2 | 27 MB | Multi-channel z-stack microscopy image |
| `20240523_Vang-1_37.tif` | TIFF | 6.7 MB | Single-plane microscopy image |
| `sub-11-YAaLR_ophys.nwb` | NWB (HDF5) | 44 MB | NeuroPAL multichannel volume (trimmed to RGB channels only) |

These files are available in the working directory (`/tmp`) automatically. If the user uploads a file with the same name, the upload overrides the bundled version.

## Environment Details

The executed Python code runs with:

- **Working directory:** `/tmp` — bundled and uploaded files are available here, so relative paths like `open('data.tif')` just work
- **Available packages:** numpy, scipy, matplotlib, tifffile, nd2reader, pynwb (and their transitive deps like h5py, hdmf)
- **`plt.savefig()`** is intercepted — instead of writing to disk, it captures the figure as a base64 PNG and includes it in the response under `plots`
- **`plt.show()`** is a no-op — it won't error or hang
- **Full `__builtins__`** are available — `open()`, `print()`, `os`, `range`, etc. all work
- **CORS** is enabled — the API can be called from any origin (localhost, Vercel, etc.)

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

## Example: Running code that uses bundled data files (no upload needed)

The following code references all three bundled data files by name. It works without any file uploads because the server pre-loads them:

```typescript
const result = await runPython(`
import os.path
import numpy as np
from nd2reader import ND2Reader
from tifffile import imread
from pynwb import NWBHDF5IO
from scipy.ndimage import zoom, gaussian_filter
import matplotlib.pyplot as plt

files = ['20191010_tail_01.nd2', '20240523_Vang-1_37.tif', 'sub-11-YAaLR_ophys.nwb']

processed_images = []
for filename in files:
    if filename.endswith('.nd2'):
        raw_data = ND2Reader(filename)
        image = np.transpose(raw_data, (1, 2, 0))
        is_normalized = False
        is_mip = False
        is_cropped = False
        zoom_level = (1, 1)
        gaussian_sigma = 0

    elif filename.endswith('.tiff') or filename.endswith('.tif'):
        image = imread(filename)
        is_normalized = False
        is_mip = True
        is_cropped = False
        zoom_level = (0.35, 0.35, 1)
        gaussian_sigma = 0.3

    elif filename.endswith('.nwb'):
        with NWBHDF5IO(filename, mode="r") as io_obj:
            nwb_file = io_obj.read()
            image_data = nwb_file.acquisition['NeuroPALImageRaw'].data[:]
            rotated_image = np.transpose(image_data, (1, 0, 2, 3))
            rgb_channel_indices = nwb_file.acquisition['NeuroPALImageRaw'].RGBW_channels[:3]
            microscopy_volume = rotated_image[:, :, :, rgb_channel_indices]
            image_dtype = microscopy_volume.dtype
            maximum_integer_value = np.iinfo(image_dtype).max
            image = (microscopy_volume / maximum_integer_value) * 255
        is_normalized = False
        is_mip = False
        is_cropped = True
        zoom_level = (1, 0.75, 1)
        gaussian_sigma = 0

    processing_steps = {}

    if not is_mip:
        dimensions = np.array(image.shape)
        if len(dimensions) < 4:
            z_index = np.argmin(dimensions)
        else:
            z_index = np.argpartition(dimensions, 1)[1]
        image = np.max(image, axis=z_index)
        processing_steps['maximum intensity projection'] = image

    if not is_normalized:
        lowest_pixel_value = np.min(image)
        highest_pixel_value = np.max(image)
        pixel_value_range = highest_pixel_value - lowest_pixel_value
        bottom_capped_image = image - lowest_pixel_value
        image = bottom_capped_image / pixel_value_range
        processing_steps['normalized'] = image

    if not is_cropped:
        background_percentile = 98
        bg = np.percentile(image, background_percentile)
        non_bg = image > bg
        row_indices = np.where(non_bg.any(axis=1))[0]
        col_indices = np.where(non_bg.any(axis=0))[0]
        row_slice = slice(row_indices[0], row_indices[-1] + 1)
        col_slice = slice(col_indices[0], col_indices[-1] + 1)
        image = image[row_slice, col_slice]
        processing_steps['cropped'] = image

    image = zoom(image, zoom_level)
    processing_steps['downsampled'] = image
    image = gaussian_filter(image, sigma=gaussian_sigma)
    processing_steps['smoothed'] = image

    file_identifier = filename.split('.')[0]
    num_images = len(processing_steps)
    fig, axes = plt.subplots(1, num_images, figsize=(4 * num_images, 3))
    current_axes = 0
    for label, image in processing_steps.items():
        axes[current_axes].imshow(image)
        axes[current_axes].set_title(label)
        current_axes += 1
    output_path = f"{file_identifier}_comparison.png"
    plt.savefig(output_path)
    processed_images.append(image)

num_images = len(processed_images)
fig, axes = plt.subplots(1, num_images, figsize=(4 * num_images, 3))
for i in range(num_images):
    filename = files[i]
    image = processed_images[i]
    axes[i].imshow(image)
    axes[i].set_title(filename)
plt.savefig("overview.png")
plt.show()
`);

// result.plots will contain:
// - "20191010_tail_01_comparison.png"
// - "20240523_Vang-1_37_comparison.png"
// - "sub-11-YAaLR_ophys_comparison.png"
// - "overview.png"
// Each is a data:image/png;base64,... URL ready for <img src>
```

## Example: Minimal call (no data files needed)

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

## Example: With user-uploaded files

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

The uploaded file is saved to `/tmp/sample.tif` and the code's working directory is `/tmp`, so `tifffile.imread("sample.tif")` resolves correctly. Uploaded files override any bundled files with the same name.

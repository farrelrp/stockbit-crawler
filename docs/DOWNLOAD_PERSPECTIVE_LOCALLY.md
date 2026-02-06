# 🔧 Download Perspective Locally (10 Minute Fix)

## Why This Is Needed

The Perspective CDN scripts load but don't expose `window.perspective`. This is a known issue with how the library is packaged. **Solution: Download and serve locally.**

---

## Quick Fix (Windows)

### Step 1: Create Directory

```batch
cd "D:\Data\Flask Saham"
mkdir static\libs\perspective
```

### Step 2: Download Files

**Open these URLs in your browser and save each file:**

1. **perspective.js**

   - URL: `https://unpkg.com/@finos/perspective@2.10.0/dist/umd/perspective.js`
   - Save to: `D:\Data\Flask Saham\static\libs\perspective\perspective.js`

2. **perspective-viewer.js**

   - URL: `https://unpkg.com/@finos/perspective-viewer@2.10.0/dist/umd/perspective-viewer.js`
   - Save to: `D:\Data\Flask Saham\static\libs\perspective\perspective-viewer.js`

3. **perspective-viewer-datagrid.js**

   - URL: `https://unpkg.com/@finos/perspective-viewer-datagrid@2.10.0/dist/umd/perspective-viewer-datagrid.js`
   - Save to: `D:\Data\Flask Saham\static\libs\perspective\perspective-viewer-datagrid.js`

4. **perspective-viewer.css**
   - URL: `https://unpkg.com/@finos/perspective-viewer@2.10.0/dist/css/themes/material.css`
   - Save to: `D:\Data\Flask Saham\static\libs\perspective\perspective-viewer.css`

**IMPORTANT:** Use `/dist/umd/` paths (Universal Module Definition), NOT `/dist/cdn/`!

---

## Alternative: PowerShell Download

```powershell
cd "D:\Data\Flask Saham"
mkdir static\libs\perspective -Force

# Download files
$baseUrl = "https://unpkg.com/@finos/perspective@2.10.0/dist/umd"
$viewerUrl = "https://unpkg.com/@finos/perspective-viewer@2.10.0/dist/umd"
$datagridUrl = "https://unpkg.com/@finos/perspective-viewer-datagrid@2.10.0/dist/umd"
$cssUrl = "https://unpkg.com/@finos/perspective-viewer@2.10.0/dist/css/themes"

Invoke-WebRequest -Uri "$baseUrl/perspective.js" -OutFile "static\libs\perspective\perspective.js"
Invoke-WebRequest -Uri "$viewerUrl/perspective-viewer.js" -OutFile "static\libs\perspective\perspective-viewer.js"
Invoke-WebRequest -Uri "$datagridUrl/perspective-viewer-datagrid.js" -OutFile "static\libs\perspective\perspective-viewer-datagrid.js"
Invoke-WebRequest -Uri "$cssUrl/material.css" -OutFile "static\libs\perspective\perspective-viewer.css"

Write-Host "✓ Downloaded all files!" -ForegroundColor Green
```

---

## Step 3: Verify Files

Check that these files exist:

```
D:\Data\Flask Saham\static\libs\perspective\
  ├── perspective.js
  ├── perspective-viewer.js
  ├── perspective-viewer-datagrid.js
  └── perspective-viewer.css
```

Each file should be 100+ KB (not empty!)

---

## Step 4: I'll Update The HTML

Once you confirm the files are downloaded, I'll update `market_replay.html` to use local files instead of CDN.

---

## Verification

After I update the HTML, refresh the page and check console:

```javascript
console.log(typeof window.perspective); // Should show "object" not "undefined"
```

---

## What Changed

**Before (CDN - broken):**

```html
<script src="https://cdn.jsdelivr.net/npm/@finos/perspective@2.10.0/dist/cdn/perspective.js"></script>
```

**After (Local - works):**

```html
<script src="{{ url_for('static', filename='libs/perspective/perspective.js') }}"></script>
```

The key difference: Using `umd` builds instead of `cdn` builds!

---

## Need Help?

If PowerShell doesn't work:

1. Manually open each URL in your browser
2. Press Ctrl+S to save
3. Save to the correct folder
4. Rename if needed (remove `.txt` extension Windows might add)

Let me know when files are downloaded! 🚀

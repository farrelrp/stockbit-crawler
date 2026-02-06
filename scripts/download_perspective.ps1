# PowerShell script to download Perspective.js files locally
# Run this with: powershell -ExecutionPolicy Bypass .\download_perspective.ps1

Write-Host "Downloading Perspective.js locally..." -ForegroundColor Cyan

# Create directory
$dir = "static\libs\perspective"
if (!(Test-Path $dir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    Write-Host "[OK] Created directory: $dir" -ForegroundColor Green
} else {
    Write-Host "[OK] Directory exists: $dir" -ForegroundColor Green
}

# File URLs (using UMD builds)
$files = @{
    "perspective.js" = "https://unpkg.com/@finos/perspective@2.10.0/dist/umd/perspective.js"
    "perspective-viewer.js" = "https://unpkg.com/@finos/perspective-viewer@2.10.0/dist/umd/perspective-viewer.js"
    "perspective-viewer-datagrid.js" = "https://unpkg.com/@finos/perspective-viewer-datagrid@2.10.0/dist/umd/perspective-viewer-datagrid.js"
    "perspective-viewer.css" = "https://unpkg.com/@finos/perspective-viewer@2.10.0/dist/css/themes/material.css"
}

# Download each file
$success = $true
foreach ($filename in $files.Keys) {
    $url = $files[$filename]
    $output = Join-Path $dir $filename
    
    Write-Host "Downloading $filename..." -ForegroundColor Yellow
    
    try {
        Invoke-WebRequest -Uri $url -OutFile $output -ErrorAction Stop
        
        # Check file size
        $size = (Get-Item $output).Length
        if ($size -gt 1000) {
            Write-Host "  [OK] Downloaded $filename ($([math]::Round($size/1KB, 2)) KB)" -ForegroundColor Green
        } else {
            Write-Host "  [ERROR] File too small ($size bytes) - may be error page" -ForegroundColor Red
            $success = $false
        }
    } catch {
        Write-Host "  [ERROR] Failed to download $filename" -ForegroundColor Red
        Write-Host "  Error: $_" -ForegroundColor Red
        $success = $false
    }
}

Write-Host ""
if ($success) {
    Write-Host "SUCCESS! All files downloaded." -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "1. Restart your Flask app (Ctrl+C and run again)" -ForegroundColor White
    Write-Host "2. I'll update market_replay.html to use local files" -ForegroundColor White
    Write-Host "3. Refresh your browser (Ctrl+F5)" -ForegroundColor White
} else {
    Write-Host "Some files failed to download." -ForegroundColor Red
    Write-Host ""
    Write-Host "Try manual download:" -ForegroundColor Yellow
    Write-Host "1. Open each URL in your browser" -ForegroundColor White
    Write-Host "2. Press Ctrl+S to save" -ForegroundColor White
    Write-Host "3. Save to: $((Get-Location).Path)\$dir" -ForegroundColor White
}

Write-Host ""
Read-Host "Press Enter to exit"

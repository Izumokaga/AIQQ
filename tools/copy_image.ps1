param(
    [Parameter(Mandatory = $true)][string]$Path
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

if (-not (Test-Path -LiteralPath $Path)) {
    Write-Error "image not found"
    exit 1
}

try {
    $data = New-Object System.Windows.Forms.DataObject
    $data.SetData([System.Windows.Forms.DataFormats]::FileDrop, [string[]]@($Path))
    if ([System.IO.Path]::GetExtension($Path).ToLowerInvariant() -ne ".gif") {
        try {
            $image = [System.Drawing.Image]::FromFile($Path)
            $data.SetImage($image)
        } catch {}
    }
    [System.Windows.Forms.Clipboard]::SetDataObject($data, $true)
    Write-Output "OK"
} catch {
    Write-Error $_.Exception.Message
    exit 2
}

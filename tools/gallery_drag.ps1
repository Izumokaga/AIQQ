param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot)
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = "AIQQ 图库 - 拖到 QQ"
$form.Width = 900
$form.Height = 620
$form.StartPosition = "CenterScreen"

$hint = New-Object System.Windows.Forms.Label
$hint.Text = "按住图片拖到 QQ 聊天窗口即可发送；右侧滚动条可查看更多。"
$hint.Dock = "Top"
$hint.Height = 28
$hint.Padding = New-Object System.Windows.Forms.Padding(10, 6, 10, 0)
$form.Controls.Add($hint)

$flow = New-Object System.Windows.Forms.FlowLayoutPanel
$flow.Dock = "Fill"
$flow.AutoScroll = $true
$flow.Padding = New-Object System.Windows.Forms.Padding(8)
$form.Controls.Add($flow)

$indexPath = Join-Path $Root "img\index.json"
if (-not (Test-Path $indexPath)) {
    [System.Windows.Forms.MessageBox]::Show("找不到图库索引：$indexPath")
    exit 1
}

try {
    $items = Get-Content $indexPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    [System.Windows.Forms.MessageBox]::Show("读取图库索引失败：$($_.Exception.Message)")
    exit 1
}

foreach ($item in @($items)) {
    $filename = [System.IO.Path]::GetFileName([string]$item.file)
    if (-not $filename) { continue }
    $path = Join-Path (Join-Path $Root "img") $filename
    if (-not (Test-Path $path)) { continue }

    $card = New-Object System.Windows.Forms.Panel
    $card.Width = 150
    $card.Height = 150
    $card.Margin = New-Object System.Windows.Forms.Padding(6)

    $picture = New-Object System.Windows.Forms.PictureBox
    $picture.Width = 138
    $picture.Height = 105
    $picture.Left = 6
    $picture.Top = 6
    $picture.SizeMode = [System.Windows.Forms.PictureBoxSizeMode]::Zoom
    $picture.Cursor = [System.Windows.Forms.Cursors]::Hand
    try {
        $picture.Image = [System.Drawing.Image]::FromFile($path)
    } catch {
        $picture.Dispose()
        continue
    }
    $picture.Tag = $path

    $label = New-Object System.Windows.Forms.Label
    $label.Text = [string]$item.name
    $label.Left = 6
    $label.Top = 115
    $label.Width = 138
    $label.Height = 28
    $label.AutoEllipsis = $true

    $picture.Add_MouseDown({
        param($sender, $eventArgs)
        if ($eventArgs.Button -ne [System.Windows.Forms.MouseButtons]::Left) { return }
        # 事件委托里 sender 不一定可用，用 $this 取当前 PictureBox 的真实本地路径。
        $source = $this
        $path = [string]$source.Tag
        if (-not $path -or -not (Test-Path -LiteralPath $path)) { return }
        try { Add-Content -LiteralPath (Join-Path $Root "logs\gallery_drag.log") -Value ("DRAG " + $path) -Encoding UTF8 } catch {}
        $data = New-Object System.Windows.Forms.DataObject
        $data.SetData([System.Windows.Forms.DataFormats]::FileDrop, [string[]]@($path))
        [void]$source.DoDragDrop($data, [System.Windows.Forms.DragDropEffects]::Copy)
    })

    $card.Controls.Add($picture)
    $card.Controls.Add($label)
    $flow.Controls.Add($card)
}

[void]$form.ShowDialog()

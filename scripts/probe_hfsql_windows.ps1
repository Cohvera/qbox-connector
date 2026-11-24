param(
    [ValidateRange(1, 65535)][int]$Port = 4900
)

$ErrorActionPreference = 'Stop'
$listenerIds = @(Get-NetTCPConnection -State Listen -LocalPort $Port |
    Select-Object -ExpandProperty OwningProcess -Unique)
if ($listenerIds.Count -ne 1) {
    throw "Expected exactly one process listening on TCP port $Port."
}

$version = Get-Process -Id $listenerIds[0] -FileVersionInfo
$executable = Split-Path -Leaf $version.FileName
if ($executable -notmatch '^manta(?:32|64)?\.exe$' -or $version.FileMajorPart -lt 1) {
    throw "The listener on TCP port $Port was not identified as a versioned HFSQL server executable."
}

[ordered]@{
    serverVersion = '{0}.{1}.{2}.{3}' -f $version.FileMajorPart, $version.FileMinorPart, $version.FileBuildPart, $version.FilePrivatePart
    fileVersion = $version.FileVersion
    productVersion = $version.ProductVersion
    executable = $executable
} | ConvertTo-Json -Compress

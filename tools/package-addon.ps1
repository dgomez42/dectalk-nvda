param(
	[string]$Version = "2026.1.0",

	[switch]$SkipRuntimeCheck
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$addonDir = Join-Path $root "addon"
$distDir = Join-Path $root "dist"
$workRoot = Join-Path $root ".build"
$workDir = Join-Path $workRoot "addon"

function Require-SynthDrivers32RuntimeFiles {
	$runtimeDir = Join-Path $workDir "synthDrivers32\dectalkRuntime\IA32"
	$required = @(
		"DECtalk.dll",
		"dtalk_us.dic",
		"lib\dtalk_us.dll"
	)
	foreach ($name in $required) {
		$path = Join-Path $runtimeDir $name
		if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
			throw "Missing synthDrivers32 runtime file: $path"
		}
	}
}

function Set-ManifestValue {
	param(
		[string]$Path,
		[string]$Name,
		[string]$Value
	)
	$content = Get-Content -LiteralPath $Path
	$content = $content | ForEach-Object {
		if ($_ -match "^\s*$([regex]::Escape($Name))\s*=") {
			"$Name = $Value"
		} else {
			$_
		}
	}
	Set-Content -LiteralPath $Path -Value $content -Encoding ASCII
}

Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $workDir, $distDir | Out-Null
Copy-Item -Path (Join-Path $addonDir "*") -Destination $workDir -Recurse -Force
Remove-Item -LiteralPath (Join-Path $workDir "synthDrivers\dectalkRuntime") -Recurse -Force -ErrorAction SilentlyContinue

$manifest = Join-Path $workDir "manifest.ini"
Set-ManifestValue -Path $manifest -Name "version" -Value $Version
Set-ManifestValue -Path $manifest -Name "minimumNVDAVersion" -Value "2019.3.0"
Set-ManifestValue -Path $manifest -Name "lastTestedNVDAVersion" -Value "2026.1.1"

if (-not $SkipRuntimeCheck) {
	Require-SynthDrivers32RuntimeFiles
}

Get-ChildItem -LiteralPath $workDir -Recurse -Force -Filter ".gitkeep" |
	Remove-Item -Force
Get-ChildItem -LiteralPath $workDir -Recurse -Force -Directory -Filter "__pycache__" |
	Remove-Item -Recurse -Force

$packageName = "dectalk-$($Version).nvda-addon"
$packagePath = Join-Path $distDir $packageName
$zipPath = Join-Path $distDir "$packageName.zip"
Remove-Item -LiteralPath $packagePath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue

Push-Location $workDir
try {
	Compress-Archive -Path "*" -DestinationPath $zipPath -Force
} finally {
	Pop-Location
}
Move-Item -LiteralPath $zipPath -Destination $packagePath -Force

Write-Host "Created $packagePath"

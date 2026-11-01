@echo off
setlocal DisableDelayedExpansion
set "IB_DIR=%~dp0"
set "IB_PARTS=%~dp0."
if not exist "%IB_PARTS%\monitoringbot-part1.zip" set "IB_PARTS=%~dp0.."
set "IB_FIRST=%IB_PARTS%\monitoringbot-part1.zip"
if not exist "%IB_FIRST%" goto missing
if not exist "%IB_PARTS%\monitoringbot-part2.zip" goto missing
if exist "%IB_DIR%monitoringbot.zip" goto exists
set "IB_TEMP=%~dp0.runtime-%RANDOM%-%RANDOM%"
if exist "%IB_TEMP%" goto scratcherror
md "%IB_TEMP%" || goto scratcherror
echo Checking Part1...
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.IO.Compression.FileSystem; $k=[IO.Compression.ZipFile]::OpenRead((Join-Path $env:IB_DIR 'package-files.zip')); try { $reader=[IO.StreamReader]::new($k.GetEntry('monitoringbot/MANIFEST.json').Open()); try { $files=(ConvertFrom-Json $reader.ReadToEnd()).files } finally { $reader.Dispose() } } finally { $k.Dispose() }; $z=[IO.Compression.ZipFile]::OpenRead($env:IB_FIRST); try { foreach ($file in $files.PSObject.Properties) { if ($file.Name -cmatch '^runtime/([a-zA-Z0-9_.-]+)$') { $leaf=$Matches[1]; $e=$z.GetEntry('monitoringbot/'+$file.Name); $out=[IO.Path]::Combine($env:IB_TEMP,$leaf); [IO.Compression.ZipFileExtensions]::ExtractToFile($e,$out); if ((Get-FileHash -LiteralPath $out -Algorithm SHA256).Hash -ne $file.Value) { throw 'Bundled runtime checksum mismatch.' } } } } finally { $z.Dispose() }"
if errorlevel 1 goto failed
"%IB_TEMP%\python.exe" -B "%~dp0build_zip.py" "%IB_PARTS%"
if errorlevel 1 goto failed
rd /s /q "%IB_TEMP%"
echo The submission ZIP is next to MAKE-ZIP.cmd.
pause
exit /b 0
:failed
rd /s /q "%IB_TEMP%"
echo Build failed. See the message above.
pause
exit /b 1
:missing
echo Save both monitoringbot-part1.zip and monitoringbot-part2.zip, using their original names, next to MAKE-ZIP.cmd or in its parent folder.
pause
exit /b 1
:exists
echo The output already exists. Move it if you want to rebuild.
pause
exit /b 1
:scratcherror
echo Cannot create the work folder. Extract this kit to a writable folder.
pause
exit /b 1

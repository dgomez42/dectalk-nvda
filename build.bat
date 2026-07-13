@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "ADDON_DIR=%ROOT%addon"
set "DIST_DIR=%ROOT%dist"
set "WORK_ROOT=%ROOT%.build"
set "WORK_DIR=%WORK_ROOT%\addon"
set "MANIFEST=%WORK_DIR%\manifest.ini"

where tar >nul 2>nul
if errorlevel 1 (
	echo Error: tar.exe was not found. Windows 10 and later include it by default.
	exit /b 1
)

if not exist "%ADDON_DIR%\synthDrivers32\dectalkRuntime\IA32\DECtalk.dll" (
	echo Missing runtime file: addon\synthDrivers32\dectalkRuntime\IA32\DECtalk.dll
	exit /b 1
)
if not exist "%ADDON_DIR%\synthDrivers32\dectalkRuntime\IA32\dtalk_us.dic" (
	echo Missing runtime file: addon\synthDrivers32\dectalkRuntime\IA32\dtalk_us.dic
	exit /b 1
)
if not exist "%ADDON_DIR%\synthDrivers32\dectalkRuntime\IA32\lib\dtalk_us.dll" (
	echo Missing runtime file: addon\synthDrivers32\dectalkRuntime\IA32\lib\dtalk_us.dll
	exit /b 1
)

if exist "%WORK_DIR%" rmdir /s /q "%WORK_DIR%"
if not exist "%WORK_ROOT%" mkdir "%WORK_ROOT%"
if not exist "%DIST_DIR%" mkdir "%DIST_DIR%"

robocopy "%ADDON_DIR%" "%WORK_DIR%" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
	echo Error: failed to copy add-on files.
	exit /b 1
)

if exist "%WORK_DIR%\synthDrivers\dectalkRuntime" rmdir /s /q "%WORK_DIR%\synthDrivers\dectalkRuntime"

for /r "%WORK_DIR%" %%F in (.gitkeep) do if exist "%%F" del /q "%%F"
for /d /r "%WORK_DIR%" %%D in (__pycache__) do if exist "%%D" rmdir /s /q "%%D"

for /f "tokens=1,* delims==" %%A in ('findstr /b /i "version =" "%MANIFEST%"') do set "VERSION=%%B"
set "VERSION=%VERSION: =%"
if "%VERSION%"=="" (
	echo Error: could not read add-on version from manifest.ini.
	exit /b 1
)

set "PACKAGE_NAME=dectalk-%VERSION%.nvda-addon"
set "PACKAGE_PATH=%DIST_DIR%\%PACKAGE_NAME%"
set "ZIP_PATH=%DIST_DIR%\%PACKAGE_NAME%.zip"

del /q "%DIST_DIR%\dectalk-*.nvda-addon" >nul 2>nul
if exist "%PACKAGE_PATH%" del /q "%PACKAGE_PATH%"
if exist "%ZIP_PATH%" del /q "%ZIP_PATH%"

pushd "%WORK_DIR%"
tar -a -c -f "%ZIP_PATH%" *
set "TAR_RESULT=%ERRORLEVEL%"
popd
if not "%TAR_RESULT%"=="0" (
	echo Error: failed to create package archive.
	exit /b %TAR_RESULT%
)

move /y "%ZIP_PATH%" "%PACKAGE_PATH%" >nul
if errorlevel 1 (
	echo Error: failed to finalize package.
	exit /b 1
)

echo Created %PACKAGE_PATH%

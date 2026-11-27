@echo off
REM Compatibility wrapper. The only implementation is release-oneclick.bat.
call "%~dp0release-oneclick.bat" %*
exit /b %ERRORLEVEL%

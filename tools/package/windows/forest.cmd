@echo off
rem ForestStudio XP command line (debugging): forest build --tile +06-076 ...  See README.md.
"%~dp0python\python.exe" -I -m forest.cli %*

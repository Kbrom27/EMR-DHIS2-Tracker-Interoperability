# Build the Windows EXE

PyInstaller must build on the same operating system as the target. To make a Windows `.exe`, run the build on a Windows computer or a Windows virtual machine.

1. Copy this project folder to Windows.
2. Install Python 3.12 for Windows. During install, enable `Add python.exe to PATH` and keep `tcl/tk and IDLE` selected.
3. Open Command Prompt in this project folder.
4. Run:

```bat
build_windows.bat
```

The executable will be created at:

```text
dist\EMR-DHIS2 Tracker interoperability App V102.exe
```

If the app still crashes on another Windows PC, rebuild once with `console=True` in `EMR_DHIS2_Tracker_Interoperability_App_V102.spec`, run the `.exe` from Command Prompt, and read the traceback. The most important warning file after a build is:

```text
build\EMR-DHIS2 Tracker interoperability App V102\warn-EMR-DHIS2 Tracker interoperability App V102.txt
```

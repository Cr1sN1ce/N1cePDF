# N1cePDF 1.0.0 — release checklist

Run on a **clean Windows 10 or 11 x64 machine or VM** (ideally one that has never had Python or N1cePDF installed;
take a snapshot first). Record the result of each line; any ✗ blocks the release.
Tester: __________  Date: __________  Windows build: __________  Installer SHA-256: __________

Legend: **[A]** is also covered by an automated script (`packaging\smoke-test.ps1`, `packaging\installer-test.ps1`,
`pytest`) — still confirm once by hand on the release candidate. Everything else is manual.

## 0. Before you start
- [ ] `build-windows.bat` finished with **BUILD SUCCEEDED** and all tests passed (no `-SkipTests`).
- [ ] `release\N1cePDF-Setup-1.0.0-x64.exe` exists; its SHA-256 matches `release\...exe.sha256`.
- [ ] Publisher set (`-Publisher`), `LICENSE` is final text, PDFium licence added to `THIRD_PARTY_NOTICES.md`.
- [ ] Test files prepared: a normal multi-page PDF, one with images, an encrypted one, a ≥1000-page one, and
      files named `договор №1.pdf`, `Țară și România.pdf` and `my report (final) v2.pdf`, stored in folders whose
      names also contain Cyrillic/Romanian letters and spaces.

## 1. Installation
| # | Check | Expected | ✓/✗ |
|---|---|---|---|
| 1.1 | Run the installer **by double-click** | Welcome page → (License page only if built with `/DWithLicensePage`) → *Select Destination Location* → *Select Additional Tasks* → *Ready to Install* → progress → finished page. SmartScreen "unknown publisher" is expected (unsigned). | |
| 1.2 | Privilege dialog at start | Offers "only for me" (default) / "for all users". Default needs no administrator password. | |
| 1.3 | **Default directory**: accept the default, finish | Installed to `%LOCALAPPDATA%\Programs\N1cePDF` (per-user). With "all users" → `C:\Program Files\N1cePDF`. | |
| 1.4 | **Custom directory**: Browse… to another folder, and separately type a path containing spaces / Cyrillic | Installs exactly there; no hard-coded user name or Downloads path anywhere. **[A]** (`installer-test.ps1` installs to a Cyrillic path) | |
| 1.5 | Tasks page | Exactly: ☑ Create a desktop shortcut, ☑ Create a Start Menu shortcut (both ticked), ☐ Offer N1cePDF in the "Open with" list (unticked). | |
| 1.6 | **Desktop shortcut enabled** | Shortcut `N1cePDF` on the desktop with the N1cePDF icon; target = installed `N1cePDF.exe`. **[A]** | |
| 1.7 | **Desktop shortcut disabled** (untick it, install) | No desktop shortcut created. | |
| 1.8 | **Start Menu shortcut enabled** | `N1cePDF` appears in Start → All apps with the N1cePDF icon. **[A]** | |
| 1.9 | **Start Menu shortcut disabled** (untick it, install) | No Start Menu entry. Both unticked → neither shortcut. | |
| 1.10 | Last page | ☑ "Launch N1cePDF" ticked by default; finishing launches the app. Untick it → app does not launch. | |
| 1.11 | Re-run the installer over the existing install | Upgrades cleanly in the same folder; shortcuts still correct; no duplicate Installed-apps entry. | |
| 1.12 | Silent install `N1cePDF-Setup-1.0.0-x64.exe /VERYSILENT /CURRENTUSER` | Installs without UI; app is **not** launched. **[A]** | |

## 2. First launch and identity
| # | Check | Expected | ✓/✗ |
|---|---|---|---|
| 2.1 | Launch from the Start Menu / desktop shortcut | Window opens in a few seconds; **no console/terminal window** at any time. **[A]** (GUI subsystem check) | |
| 2.2 | Icons | N1cePDF icon on: window title bar, taskbar button, desktop shortcut, Start Menu entry, `N1cePDF.exe` in Explorer, installer file, Installed apps entry. Pin to taskbar → the pinned icon and the running window share one button. | |
| 2.3 | Explorer → `N1cePDF.exe` → Properties → Details | Product name N1cePDF, version 1.0.0.0. | |
| 2.4 | Welcome screen | Logo, Open PDF, Create Blank PDF, recent list (empty). | |
| 2.5 | **Installation without Python** | On a machine with no Python installed (or with Python removed from PATH) the app starts and every check below passes. **[A]** partial: smoke test strips Python from PATH/`PYTHON*`. | |
| 2.6 | **Offline usage** | Disconnect network / enable airplane mode; reboot optional; run sections 3–5. Nothing prompts for or needs a connection. (Optional: Resource Monitor → Network shows no N1cePDF activity.) | |
| 2.7 | Packaged self-test | `powershell -File packaging\smoke-test.ps1 -ExeDir "<install folder>"` prints **SMOKE TEST PASSED**. **[A]** | |
| 2.8 | Record size/startup | Installed size: ____ MB. Cold start to visible window: ____ s (stopwatch). Idle memory (Task Manager): ____ MB. | |

## 3. Opening, editing, saving
| # | Check | Expected | ✓/✗ |
|---|---|---|---|
| 3.1 | Ctrl+O → open a normal PDF | Opens; pages render; thumbnails/bookmarks/search work. | |
| 3.2 | **Drag-and-drop** a PDF from Explorer onto the window (empty welcome screen, and again with a document open) | Opens in a (new) tab. Dropping the same file again just focuses its tab. | |
| 3.3 | **Cyrillic** file/folder names: open `договор №1.pdf` | Opens; title/tab show the name correctly. | |
| 3.4 | **Romanian** names: open `Țară și România.pdf`; file with spaces/parentheses | Same. | |
| 3.5 | Add text with Cyrillic **and** Romanian characters (`Привет мир Țară și România ăâîșț`) | Text appears correctly in the editor; after saving, the text displays and can be searched/copied in N1cePDF and in another PDF reader (Edge/Acrobat). | |
| 3.6 | Add image, rectangle/arrow/ellipse, highlight/underline/strikethrough, ink, comment | Each appears; selectable, movable, deletable. | |
| 3.7 | Edit existing text (standard-font file) and replace-workflow text (embedded font) | Preview dialog lists warnings; result as previewed. | |
| 3.8 | **Undo / Redo** (Ctrl+Z, Ctrl+Y, Ctrl+Shift+Z) after 5+ different edits, page rotate/insert/delete | Each step reverses/replays exactly; "modified" marker (●) clears when you undo back to the saved state. | |
| 3.9 | **Save** (Ctrl+S) on an opened file | Asks before overwriting; afterwards the title loses the ● marker; original replaced only after a successful save. | |
| 3.10 | **Save As** to a new name (incl. Cyrillic/spaces), then Save As onto an existing file | Original untouched by Save As; overwrite is confirmed. | |
| 3.11 | **Reopen after saving**: close the document, open the saved file | All text, images, shapes and annotations are still there and editable-as-saved; page count unchanged. | |
| 3.12 | Open the saved file in a second reader (Edge/Acrobat) | Renders identically; annotations show. | |
| 3.13 | Save over a file that is **open in another program** | Clear message ("access was denied … not open in another program"); original intact; your edits still in the window. | |
| 3.14 | Save into a read-only folder / full USB stick removed mid-way | Clear error, no data loss, no crash. | |
| 3.15 | Encrypted PDF | Password prompt; saving warns it will be unencrypted. | |
| 3.16 | **Large document** (≥1000 pages, and one with big images) | Opens, scrolls, searches, saves without freezing the UI for long; memory stays reasonable. Time to open/save: ____ / ____ s. | |
| 3.17 | **Accidental data loss** | Closing a modified tab or the window prompts Save / Discard / Cancel; Cancel keeps everything; Discard leaves the file on disk unchanged. | |
| 3.18 | Window close while a save/render is running | Closes cleanly (no crash dialog, no `N1cePDF.exe` left in Task Manager). | |

## 4. Windows Explorer integration
| # | Check | Expected | ✓/✗ |
|---|---|---|---|
| 4.1 | Install **without** the "Open with" task | `HKCU\Software\Classes\N1cePDF.Document` does not exist; right-click PDF → Open with lists N1cePDF only if you browse to it ("Choose another app"). **[A]** (registry check) | |
| 4.2 | Install **with** the task | Right-click a PDF → Open with → **N1cePDF** is listed (may need "Choose another app" the first time); N1cePDF appears in Settings → Apps → Default apps (search "N1cePDF"/.pdf). | |
| 4.3 | Open with → N1cePDF on a file with spaces, Cyrillic and Romanian characters in its path | Opens that file in N1cePDF. | |
| 4.4 | Default app untouched | Your existing default PDF application is unchanged after install (check Settings → Default apps → .pdf). Setting N1cePDF as default yourself works and double-click opens files in N1cePDF. | |
| 4.5 | Select 3 PDFs → Enter / Open with | Each opens (one window per file in 1.0.0 — documented limitation). | |
| 4.6 | Command line: `"<install>\N1cePDF.exe" "C:\path with spaces\документ.pdf"` | Opens the file. A non-existent path gives an error dialog, not a crash. | |

## 5. Uninstallation
| # | Check | Expected | ✓/✗ |
|---|---|---|---|
| 5.1 | Settings → Apps → Installed apps | **N1cePDF**, version 1.0.0, correct publisher, N1cePDF icon. | |
| 5.2 | Uninstall (leave the app running first) | Installer offers to close the running app; completes. | |
| 5.3 | Prompt "Also delete your N1cePDF settings … ?" | Default button is **No**; choosing No keeps `%APPDATA%\N1cePDF` and `%LOCALAPPDATA%\N1cePDF`; choosing Yes removes both. Silent uninstall never deletes them. | |
| 5.4 | After uninstall | Install folder gone (or empty), desktop and Start Menu shortcuts gone, `HKCU\Software\Classes\N1cePDF.Document`, `HKCU\Software\N1cePDF` and the `RegisteredApplications` value gone, no services/scheduled tasks ever created. **[A]** (files + shortcuts) | |
| 5.5 | Your PDFs | All user documents (including any saved by N1cePDF) are untouched. **[A]** | |
| 5.6 | Reinstall after uninstall | Works; recent list present only if you kept settings. | |

## 6. Optional end-to-end automation (current-user install, then removed)
    powershell -ExecutionPolicy Bypass -File packaging\installer-test.ps1 -Installer release\N1cePDF-Setup-1.0.0-x64.exe
Must end with **INSTALLER TEST PASSED**. It covers 1.4, 1.6, 1.8, 1.12, 2.7, 4.1, 5.4 (files/shortcuts), 5.5 only.

## 7. Sign-off
- [ ] All lines ✓ (list any waived item and why): ______________________________________
- [ ] Installer SHA-256 recorded and published next to the download.
- [ ] Known limitations (`docs/known-limitations.md`) reviewed and acceptable for 1.0.0.

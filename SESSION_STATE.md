# Session State & Conversation Record

This file records the working session for the **EMR-DHIS2 Tracker Interoperability** project.
If the session is interrupted, read this file to resume from where we stopped.

---

## 1. Project Overview

- **Project**: EMR-DHIS2 Tracker Interoperability (Python/Tkinter desktop app)
- **Location (this repo)**: `/home/kbrom/Work/IMNID/IMNID Developments/EMR-DHIS2 Tracker Interoperability`
- **Purpose**: Bridges an OpenMRS EMR with DHIS2 Tracker:
  - Export OpenMRS patient data by visit type & date
  - Transform the export CSV into DHIS2 Tracker CSV using mapping files (Excel) + dictionary
  - Import the transformed CSV into DHIS2
  - Full direct sync (export -> transform -> import in one step)
- **Reference (older/original project)**: `/home/kbrom/Work/IMNID/EMR - DHIS2/Interoperability/EMR-DHIS2 Tracker sync May 11`

### Two independent workflows (after restructure)
1. **OpenMRS (Bahmni)** — original workflow. Uses user-selected/imported mapping Excel file + dictionary for transform. Sub-steps: EMR Data Export, Transform CSV, Import to DHIS2, EMR-DHIS2 Tracker Sync.
2. **OpenMRS 3 (O3)** — separate workflow. Does NOT ask user to upload mapping files; uses stored/generated mapping files. Self-contained in the `o3app/` package.

**Requirement**: The two workflows must be fully separate and NOT share scripts.

---

## 2. How the App Starts / Runs

- Entry point: `main.py` (`python main.py`).
- Main menu has TWO tiles: **OpenMRS (Bahmni)** and **OpenMRS 3 (O3)**.
- Bahmni tile -> shows 4-card sub-menu -> each card routes to the top-level UI pages.
- O3 tile -> routes to the O3 workflow page.

### Navigation wiring in `main.py`
- `show_source_menu()` — the two-tile main menu.
- `show_bahmni_menu()` — the 4-card Bahmni sub-menu (Export/Transform/Import/Sync).
- `show_o3_page()` — launches `o3app.ui.o3_page.O3Page(view=None, ...)`.
- Bahmni pages return via `show_bahmni_menu`; O3 page returns via `show_source_menu`.

---

## 3. Current File Structure

### Top-level (Bahmni workflow — kept "identical to May 11" for logic)
- `config.py`        — KEPT CURRENT (has `FACILITIES`/`FACILITY_CODES`, O3 constants, `normalize_stage_name`, `normalize_program_value`). May 11 version is smaller; keeping current to not break UI.
- `utils.py`         — KEPT CURRENT (superset). May 11 is missing `require_xlsx_file`/`require_value_mapping_csv` used by current UI pages.
- `models.py`        — already identical to May 11.
- `clients/`         — `openmrs_client.py`, `dhis2_client.py` = May 11 copies.
- `export/extractors.py` — export logic (untouched in restore).
- `transform/`       — `mapping.py`, `pipeline.py`, `matcher.py`, `normalizers.py` = May 11 copies. **`investigations.py` DELETED** (did not exist in May 11).
- `import_/`         — `importer.py`, `payload_builder.py` = May 11 copies.
- `rules/`           — `tracker_mapping_rules.py` = May 11 copy.
- `ui/`              — `export_page.py`, `transform_page.py`, `import_page.py`, `sync_page.py`, `components.py` — CURRENT versions (kept). They call `transform_rows()`, `import_rows()`, `set_mapping_files()`.

### `o3app/` (fully self-contained O3 workflow — NEW package)
- `config.py` (RESOURCES_DIR points to repo root `Resources/` via `parents[1]`)
- `utils.py`, `models.py`
- `clients/` (`openmrs_client.py`, `dhis2_client.py`)
- `export/extractors.py`
- `transform/` (`mapping.py`, `pipeline.py`, `matcher.py`, `normalizers.py`, `investigations.py`)
- `import_/` (`importer.py`, `payload_builder.py`)
- `rules/tracker_mapping_rules.py`
- `schemas.py`, `mappings.py`, `extract.py`, `export_unmapped_report.py`
- `ui/o3_page.py`, `ui/components.py`
- All internal imports rewritten to `o3app.*`. Verified NO imports point outside the package.

### Shared data folder
- `Resources/` (data files, not scripts) is SHARED at repo root. Both configs point to it.

---

## 4. Session Conversation Log

1. User asked: what is this project + what are the menus when opening the app.
   - Answered: describes app; main menu has 5 cards (Export, Transform, Import, Sync, O3 Workflow).

2. User explained history:
   - Original project (May 11) had no O3 workflow. O3 added later.
   - After adding O3, the Transform/Import/Sync menus had many errors.
   - Wants restructure: Bahmni and O3 workflows SEPARATE, not sharing scripts.
   - Bahmni uses imported mapping files + dictionary to transform; O3 does not ask for a file upload.
   - Main menu should ask Bahmni or O3 first; "1-4" menu + their scripts run if Bahmni; O3 scripts run if O3 selected.

3. User answered 3 clarifying questions:
   - Shared low-level libs (config/utils/models/clients/import) -> **Copy into two separate sets** (fully self-contained).
   - O3 transform/import -> **Yes, fully separate copies**.
   - Main menu layout -> **Two big tiles: Bahmni vs O3** (reveal the existing 4 Bahmni cards).

4. I performed the restructure:
   - Created `o3app/` fully self-contained package with copies of all shared modules.
   - Moved O3-specific logic (`extract`, `mappings`, `schemas`, `export_unmapped_report`) into `o3app/`.
   - Created `o3app/ui/o3_page.py` with imports rewritten to `o3app.*`.
   - Rewrote `main.py` with two-tile menu + Bahmni sub-menu routing.
   - Verified: imports OK, GUI smoke test passed, O3 mapping generation works (30 forms, 388 maternal + 484 neonatal mapping rows, 968 value rows).

5. User questioned whether Bahmni part is "exactly the same as May 11".
   - I compared; found differences. Explained those differences were PRE-EXISTING enhancements in this repo (not caused by me): facility dropdown, unmatched-fields report, better matcher, etc.

6. User clarified: "the way it transforms and imports and syncs should be the same."
   - I verified transform/import/sync modules were untouched by my restructure (git diff empty). Confirmed the only UI-page changes were pre-existing uncommitted edits (validation added to sync_page.py / transform_page.py).

7. User: "when you compare with May 11" -> I compared against the literal May 11 reference:
   - `import_/importer.py`, `models.py` IDENTICAL.
   - `transform/mapping.py`, `matcher.py`, `pipeline.py`, `normalizers.py` DIFFER (enhanced in current repo).
   - `import_/payload_builder.py`, `clients/dhis2_client.py`, `clients/openmrs_client.py`, `utils.py`, `config.py` DIFFER.
   - `transform/investigations.py` did NOT exist in May 11.

8. User: "make it exactly the same as the may 11."
   - User chose scope: **Only the transform/import logic** (keep current UI pages, config.py, facility dropdown).
   - I replaced with May 11 byte-identical copies:
     - transform/{mapping,pipeline,matcher,normalizers}.py
     - import_/{payload_builder,importer}.py
     - clients/{dhis2_client,openmrs_client}.py
     - rules/tracker_mapping_rules.py
     - **Deleted transform/investigations.py**
   - KEPT current: utils.py (superset, needed by UI), config.py (FACILITIES + O3 constants), UI pages, o3app.
   - Verified: all logic files byte-identical to May 11; all compile; GUI smoke test OK; real transform test on `Resources/AXRH Delivery May 22.csv` produced 1 maternal row / 14KB output.

9. User reported (pre-split) Bahmni sync error: `"EMR-DHIS2 Tracker value mappings Meki value mappings.csv" is not a valid .xlsx workbook`.
   - Meaning: user put the value-mappings CSV into the Mapping/Dictionary Excel field. The transform code tried to open the CSV as an xlsx (zip) in `read_xlsx_rows()` -> `BadZipFile` -> `RuntimeError("...is not a valid .xlsx workbook.")` at `utils.py:223`.
   - Root cause: field/type mismatch (user error) shown with a cryptic message; happened before the `require_xlsx_file` guard existed.
   - Now guarded: `ui/transform_page.py:217` and `ui/sync_page.py:370` call `require_xlsx_file` for Mapping/Dictionary and `require_value_mapping_csv` for the Value Mapping field BEFORE running. Friendly message tells users the value-mapping CSV belongs only in "Value Mapping CSV (Optional)".
   - Fix for users: mapping `.xlsx` -> "Mapping Excel File", dictionary `.xlsx` -> "Dictionary Excel File", value-mappings `.csv` -> "Value Mapping CSV (Optional)" only.

10. Discussion: accept `.xlsx` OR `.csv` in the Value Mapping field?
    - Possible with a small localized change: relax `require_value_mapping_csv` (utils.py) + type-detect branch in `load_external_value_rules()` (rules/tracker_mapping_rules.py), assuming the xlsx has the same columns. Mapping/Dictionary fields accepting CSV would be more invasive (transform pipeline is xlsx-zip-based).
    - **Decision: NOT doing it.** Keep Value Mapping as CSV-only (one unambiguous format); instead improve messaging if a user drops an xlsx there. Better to clarify the error than support two formats.

---

## 5. Current Todo List

**ALL TASKS ARE COMPLETED.**

| # | Task | Status |
|---|------|--------|
| 1 | Map all differences between current Bahmni and May 11 reference | ✅ Done |
| 2 | Create `o3app/` package with self-contained copies of shared libs | ✅ Done |
| 3 | Isolate O3-specific modules into `o3app/` | ✅ Done |
| 4 | Create `o3app/ui/o3_page.py` using the isolated package | ✅ Done |
| 5 | Rewrite `main.py` with two-tile menu (Bahmni vs O3) | ✅ Done |
| 6 | Restore Bahmni transform/ modules to May 11 (mapping, matcher, pipeline, normalizers; delete investigations) | ✅ Done |
| 7 | Restore import_/ and clients/ to May 11 (payload_builder, dhis2_client, openmrs_client) | ✅ Done |
| 8 | Restore rules/tracker_mapping_rules.py to May 11 | ✅ Done |
| 9 | Keep utils.py and config.py current (for UI compatibility) | ✅ Done |
| 10 | Verify app builds & imports after restore | ✅ Done |

---

## 6. Completed Tasks (summary of outcomes)

1. **Separated Bahmni & O3 into independent workflows** — no shared scripts.
2. **O3 workflow isolated** into the self-contained `o3app/` package (verified zero external imports).
3. **New two-tile main menu**: OpenMRS (Bahmni) / OpenMRS 3 (O3).
4. **May 11 parity** for Bahmni transform/import/sync logic — byte-identical copies confirmed.
5. **Deliberate keeps** (documented above): current `utils.py`, current `config.py`, current UI pages, whole `o3app/`.
6. **Verified end-to-end**: imports fine, all modules compile, GUI smoke test passed for all screens, real transform produced correct output.

---

## 7. Key Decisions / Notes for Future Work

- `Resources/` stays SHARED (data, not code). Both `config.py` and `o3app/config.py` resolve to repo-root `Resources/`.
- `o3app/config.py` uses `Path(__file__).resolve().parents[1] / "Resources"` for RESOURCES_DIR.
- If May 11 `utils.py` is ever wanted verbatim, the UI pages that call `require_xlsx_file`/`require_value_mapping_csv` must also be reverted (those helpers do not exist in May 11 utils).
- `transform/investigations.py` is gone from the Bahmni side; if neonate investigation handling is needed again, it must be re-added (it did not exist in May 11).
- O3 mapping files are stored in `Resources/O3/` (generated once): `EMR-DHIS2 Tracker O3 Maternal Mapping.xlsx`, `EMR-DHIS2 Tracker O3 Neonatal Mapping.xlsx`, `EMR-DHIS2 Tracker O3 Value Mappings.csv`. Form schemas + metadata also under `Resources/O3/`.

---

## 8. If Interrupted / Resume Instructions

If work was interrupted or you are returning to this project:
1. Read THIS file first.
2. Confirm `git status` to see current working-tree state.
3. Resume from Section 5 (Todo List) / Section 6 (Completed) / Section 7 (Decisions).
4. Run a quick sanity check: `python -c "import main"` and the GUI smoke test, then continue.
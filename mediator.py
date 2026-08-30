"""
EMR-DHIS2 Interoperability Mediator Service
----------------------------------------------
A lightweight, high-performance REST Mediator service built with FastAPI.
Allows testing the full end-to-end EMR to DHIS2 Tracker pipeline locally
and deploying to a server (or Docker / OpenHIM) later.

Interactive API Documentation:
- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc:      http://127.0.0.1:8000/redoc
"""

import csv
import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
import uvicorn
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, status
from fastapi.middleware.cors import CORSMiddleware

# Core EMR-DHIS2 Utility Modules
from clients.openmrs_client import ApiClient, normalize_base_url
from config import MATERNAL_PROGRAM, NEONATAL_PROGRAM, O3_SCHEMA_ROOT
from o3.mappings import DEFAULT_MATERNAL_DICTIONARY, DEFAULT_NEONATAL_DICTIONARY
from export.extractors import (
    determine_program_from_visit_type,
    get_patients_by_visit_type,
    normalize_date_filter,
    validate_date_range,
    write_patients_csv,
)
from import_.importer import import_rows
from transform.mapping import set_mapping_files
from transform.pipeline import transform_rows
from utils import read_xlsx_rows

# O3 Specific Modules
from o3.extract import (
    determine_program_from_visit_type as determine_o3_program,
    get_patients_by_visit_type as get_o3_patients,
    write_o3_patients_csv,
)
from o3.schemas import FormRegistry, load_default_forms
from o3app.transform.pipeline import transform_rows as transform_o3_rows

tags_metadata = [
    {
        "name": "Pipeline Synchronization",
        "description": "Trigger end-to-end sync for Bahmni (OpenMRS 2.x) and OpenMRS 3.x (O3).",
    },
    {
        "name": "Sync Status & Resumption",
        "description": "Inspect live sync progress, view detailed error logs per record, and resume interrupted syncs.",
    },
    {
        "name": "Mapping Management",
        "description": "View, edit, and upload Variable and Value mappings for Bahmni and O3.",
    },
    {
        "name": "System Health",
        "description": "Service root and health check endpoints.",
    },
]

app = FastAPI(
    title="EMR-DHIS2 Interoperability Mediator",
    description="Local & Server Interoperability Mediator for OpenMRS (Bahmni / O3) to DHIS2 Tracker",
    version="1.2.0",
    openapi_tags=tags_metadata,
)

# Enable CORS for local testing and dashboard integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CHECKPOINT_FILE = Path("sync_checkpoint.json")


# Request & Mapping Models
class BahmniSyncRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "emr_base_url": "http://localhost:8080/openmrs",
                "emr_username": "superman",
                "emr_password": "Admin123",
                "facility_code": "1001",
                "visit_type_name": "Delivery",
                "start_date": "2026-01-01",
                "end_date": "2026-08-30",
                "dhis2_url": "https://imnid.mohdigitalhealth.gov.et",
                "dhis2_username": "admin",
                "dhis2_password": "district",
                "mapping_file_path": None,
                "value_mapping_file_path": None,
                "output_dir": None,
            }
        }
    }

    emr_base_url: str = Field(default="http://localhost:8080/openmrs", description="OpenMRS Server Base URL / IP")
    emr_username: str = Field(default="superman")
    emr_password: str = Field(default="Admin123")
    facility_code: str = Field(default="1001", description="MFL / OrgUnit Code")
    visit_type_name: str = Field(default="Delivery", description="Visit type name (e.g. Delivery, Labour, Obs, NICU)")
    start_date: Optional[str] = Field(default=None)
    end_date: Optional[str] = Field(default=None)
    dhis2_url: str = Field(default="https://imnid.mohdigitalhealth.gov.et")
    dhis2_username: str = Field(default="admin")
    dhis2_password: str = Field(default="district")
    mapping_file_path: Optional[str] = Field(default=None, description="Optional custom path to Variable Mapping Excel file")
    value_mapping_file_path: Optional[str] = Field(default=None, description="Optional custom path to Value Mapping CSV file")
    output_dir: Optional[str] = Field(default=None, description="Optional custom directory to store generated CSV files")


class O3SyncRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "emr_base_url": "http://localhost:8080/openmrs",
                "emr_username": "superman",
                "emr_password": "Admin123",
                "facility_code": "1001",
                "visit_type_name": "Delivery",
                "start_date": "2026-01-01",
                "end_date": "2026-08-30",
                "dhis2_url": "https://imnid.mohdigitalhealth.gov.et",
                "dhis2_username": "admin",
                "dhis2_password": "district",
                "mapping_file_path": None,
                "value_mapping_file_path": None,
                "output_dir": None,
            }
        }
    }

    emr_base_url: str = Field(default="http://localhost:8080/openmrs", description="OpenMRS Server Base URL / IP")
    emr_username: str = Field(default="superman")
    emr_password: str = Field(default="Admin123")
    facility_code: str = Field(default="1001", description="MFL / OrgUnit Code")
    visit_type_name: str = Field(default="Delivery", description="Visit type name (e.g. Delivery, Labour, Obs, NICU)")
    start_date: Optional[str] = Field(default=None)
    end_date: Optional[str] = Field(default=None)
    dhis2_url: str = Field(default="https://imnid.mohdigitalhealth.gov.et")
    dhis2_username: str = Field(default="admin")
    dhis2_password: str = Field(default="district")
    mapping_file_path: Optional[str] = Field(default=None, description="Optional custom path to Variable Mapping Excel file")
    value_mapping_file_path: Optional[str] = Field(default=None, description="Optional custom path to Value Mapping CSV file")
    output_dir: Optional[str] = Field(default=None, description="Optional custom directory to store generated CSV files")


class ResumeSyncRequest(BaseModel):
    dhis2_url: Optional[str] = Field(default="https://imnid.mohdigitalhealth.gov.et")
    dhis2_username: Optional[str] = Field(default="admin")
    dhis2_password: Optional[str] = Field(default="district")


class VariableMappingItem(BaseModel):
    stage_name: str = Field(..., description="DHIS2 Program Stage Name")
    data_element_name: str = Field(..., description="DHIS2 Data Element Name")
    source_name: str = Field(..., description="EMR Concept Name / Column")


class ValueMappingItem(BaseModel):
    program: Optional[str] = Field(default="", description="DHIS2 Program Name")
    target_header: str = Field(..., description="Stage Name :: Data Element Name")
    source_value: str = Field(..., description="EMR Value")
    dhis2_value: str = Field(..., description="DHIS2 Option Code / Value")
    transform: Optional[str] = Field(default="", description="Optional date/time/text transform")


class MappingUpdateRequest(BaseModel):
    program: str = Field(default="maternal", description="Program type: 'maternal' or 'neonatal'")
    variable_mappings: Optional[List[VariableMappingItem]] = Field(default=None, description="List of variable mappings to update")
    value_mappings: Optional[List[ValueMappingItem]] = Field(default=None, description="List of value mappings to update")


@app.get("/", tags=["System Health"])
def root():
    return {
        "service": "EMR-DHIS2 Interoperability Mediator",
        "status": "running",
        "documentation": "/docs",
        "version": "1.2.0",
    }


@app.get("/health", tags=["System Health"])
def health_check():
    return {"status": "healthy", "timestamp": time.time()}


# --- Checkpoint & State Management Helpers ---

def _save_checkpoint(data: Dict):
    data["updated_at"] = datetime.now().isoformat()
    CHECKPOINT_FILE.write_text(json.dumps(data, indent=2))


def _load_checkpoint() -> Dict:
    if not CHECKPOINT_FILE.is_file():
        return {
            "status": "idle",
            "message": "No sync checkpoint recorded yet.",
            "can_resume": False,
        }
    try:
        return json.loads(CHECKPOINT_FILE.read_text())
    except Exception:
        return {"status": "unknown", "can_resume": False}


def _parse_issues_csv(log_path: Path) -> List[Dict[str, str]]:
    if not log_path.is_file():
        return []
    issues = []
    with log_path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            issues.append({
                "record_id": row.get("record_id", ""),
                "patient": row.get("patient", ""),
                "program": row.get("program", ""),
                "stage": row.get("stage", ""),
                "column": row.get("column", ""),
                "field_name": row.get("field_name", ""),
                "value": row.get("value", ""),
                "reason": row.get("reason", ""),
            })
    return issues


def _pick_mapping_files(program_value: str, custom_mapping: Optional[str] = None, custom_value: Optional[str] = None):
    mapping_dir = Path("Resources")
    if custom_mapping and Path(custom_mapping).is_file():
        mapping_path = Path(custom_mapping)
    else:
        if program_value == MATERNAL_PROGRAM:
            mapping_path = mapping_dir / "EMR-DHIS2 Tracker Maternal Mapping.xlsx"
            if not mapping_path.is_file():
                mapping_path = mapping_dir / "EMR-DHIS2 Tracker MID Mapping.xlsx"
        else:
            mapping_path = mapping_dir / "EMR-DHIS2 Tracker Neonatal Mapping.xlsx"
            if not mapping_path.is_file():
                mapping_path = mapping_dir / "EMR-DHIS2 Tracker NID Mapping.xlsx"

    if program_value == MATERNAL_PROGRAM:
        dictionary_path = DEFAULT_MATERNAL_DICTIONARY
        if not dictionary_path.is_file():
            dictionary_path = mapping_dir / "MID data disctionary.xlsx"
    else:
        dictionary_path = DEFAULT_NEONATAL_DICTIONARY
        if not dictionary_path.is_file():
            dictionary_path = mapping_dir / "NCF data disctionary.xlsx"

    if custom_value and Path(custom_value).is_file():
        value_path = Path(custom_value)
    else:
        if program_value == MATERNAL_PROGRAM:
            value_path = mapping_dir / "EMR-DHIS2 Tracker Maternal Value Mappings.csv"
            if not value_path.is_file():
                value_path = mapping_dir / "EMR-DHIS2 Tracker Value Mappings.csv"
        else:
            value_path = mapping_dir / "EMR-DHIS2 Tracker Neonatal Value Mappings.csv"
            if not value_path.is_file():
                value_path = mapping_dir / "EMR-DHIS2 Tracker Value Mappings.csv"

    for path in (mapping_path, dictionary_path, value_path):
        if not path.is_file():
            raise RuntimeError(f"Required mapping file not found: {path}")

    return mapping_path, dictionary_path, value_path


def _pick_o3_mapping_files(program_value: str, custom_mapping: Optional[str] = None, custom_value: Optional[str] = None):
    o3_dir = Path("Resources/O3")
    if custom_mapping and Path(custom_mapping).is_file():
        mapping_path = Path(custom_mapping)
    else:
        if program_value == MATERNAL_PROGRAM:
            mapping_path = o3_dir / "EMR-DHIS2 Tracker O3 Maternal Mapping.xlsx"
        else:
            mapping_path = o3_dir / "EMR-DHIS2 Tracker O3 Neonatal Mapping.xlsx"

    if program_value == MATERNAL_PROGRAM:
        dictionary_path = DEFAULT_MATERNAL_DICTIONARY
        if not dictionary_path.is_file():
            dictionary_path = Path("Resources/MID data disctionary.xlsx")
    else:
        dictionary_path = DEFAULT_NEONATAL_DICTIONARY
        if not dictionary_path.is_file():
            dictionary_path = Path("Resources/NCF data disctionary.xlsx")

    if custom_value and Path(custom_value).is_file():
        value_path = Path(custom_value)
    else:
        value_path = o3_dir / "EMR-DHIS2 Tracker O3 Value Mappings.csv"

    for path in (mapping_path, dictionary_path, value_path):
        if not path.is_file():
            raise RuntimeError(f"Required O3 mapping file not found: {path}")

    return mapping_path, dictionary_path, value_path


# --- Mapping Management API Endpoints ---

def _read_variable_mappings(excel_path: Path) -> List[VariableMappingItem]:
    rows = read_xlsx_rows(excel_path)
    if not rows:
        return []
    headers = [h.strip() for h in rows[0]]
    stage_idx = next((i for i, h in enumerate(headers) if "stage" in h.lower()), 0)
    data_elem_idx = next((i for i, h in enumerate(headers) if "element" in h.lower()), 2)
    source_idx = next((i for i, h in enumerate(headers) if "concept" in h.lower() or "source" in h.lower() or i not in (stage_idx, data_elem_idx)), 1)

    items = []
    for row in rows[1:]:
        if len(row) > max(stage_idx, data_elem_idx, source_idx):
            stage = str(row[stage_idx] or "").strip()
            de = str(row[data_elem_idx] or "").strip()
            src = str(row[source_idx] or "").strip()
            if stage and de:
                items.append(VariableMappingItem(stage_name=stage, data_element_name=de, source_name=src))
    return items


def _write_variable_mappings(excel_path: Path, items: List[VariableMappingItem]):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Mappings"

    headers = ["DHIS2 Program Stage Name", "EMR Concept Name", "DHIS2 Data Element Name"]
    ws.append(headers)

    for item in items:
        ws.append([item.stage_name, item.source_name, item.data_element_name])

    wb.save(excel_path)


def _read_value_mappings(csv_path: Path) -> List[ValueMappingItem]:
    if not csv_path.is_file():
        return []
    items = []
    with csv_path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            items.append(
                ValueMappingItem(
                    program=str(row.get("program") or "").strip(),
                    target_header=str(row.get("target_header") or "").strip(),
                    source_value=str(row.get("source_value") or "").strip(),
                    dhis2_value=str(row.get("dhis2_value") or "").strip(),
                    transform=str(row.get("transform") or "").strip(),
                )
            )
    return items


def _write_value_mappings(csv_path: Path, items: List[ValueMappingItem]):
    fieldnames = ["program", "target_header", "source_value", "dhis2_value", "transform"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            writer.writerow({
                "program": item.program,
                "target_header": item.target_header,
                "source_value": item.source_value,
                "dhis2_value": item.dhis2_value,
                "transform": item.transform,
            })


@app.get("/api/v1/mappings/bahmni", tags=["Mapping Management"])
def get_bahmni_mappings(program: str = Query(default="maternal", description="Program type: 'maternal' or 'neonatal'")):
    """
    Get variable and value mappings for OpenMRS 2.x (Bahmni)
    """
    prog_key = MATERNAL_PROGRAM if program.lower().startswith("m") else NEONATAL_PROGRAM
    m_path, d_path, v_path = _pick_mapping_files(prog_key)

    return {
        "system": "Bahmni (OpenMRS 2.x)",
        "program": prog_key,
        "mapping_file": str(m_path),
        "value_mapping_file": str(v_path),
        "variable_mappings": _read_variable_mappings(m_path),
        "value_mappings": _read_value_mappings(v_path),
    }


@app.put("/api/v1/mappings/bahmni", tags=["Mapping Management"])
def update_bahmni_mappings(req: MappingUpdateRequest):
    """
    Update variable or value mappings for OpenMRS 2.x (Bahmni)
    """
    prog_key = MATERNAL_PROGRAM if req.program.lower().startswith("m") else NEONATAL_PROGRAM
    m_path, d_path, v_path = _pick_mapping_files(prog_key)

    updated_vars = 0
    updated_vals = 0

    if req.variable_mappings is not None:
        _write_variable_mappings(m_path, req.variable_mappings)
        updated_vars = len(req.variable_mappings)

    if req.value_mappings is not None:
        _write_value_mappings(v_path, req.value_mappings)
        updated_vals = len(req.value_mappings)

    return {
        "status": "success",
        "system": "Bahmni (OpenMRS 2.x)",
        "program": prog_key,
        "updated_variable_mappings": updated_vars,
        "updated_value_mappings": updated_vals,
    }


@app.get("/api/v1/mappings/o3", tags=["Mapping Management"])
def get_o3_mappings(program: str = Query(default="maternal", description="Program type: 'maternal' or 'neonatal'")):
    """
    Get variable and value mappings for OpenMRS 3.x (O3)
    """
    prog_key = MATERNAL_PROGRAM if program.lower().startswith("m") else NEONATAL_PROGRAM
    m_path, d_path, v_path = _pick_o3_mapping_files(prog_key)

    return {
        "system": "OpenMRS 3.x (O3)",
        "program": prog_key,
        "mapping_file": str(m_path),
        "value_mapping_file": str(v_path),
        "variable_mappings": _read_variable_mappings(m_path),
        "value_mappings": _read_value_mappings(v_path),
    }


@app.put("/api/v1/mappings/o3", tags=["Mapping Management"])
def update_o3_mappings(req: MappingUpdateRequest):
    """
    Update variable or value mappings for OpenMRS 3.x (O3)
    """
    prog_key = MATERNAL_PROGRAM if req.program.lower().startswith("m") else NEONATAL_PROGRAM
    m_path, d_path, v_path = _pick_o3_mapping_files(prog_key)

    updated_vars = 0
    updated_vals = 0

    if req.variable_mappings is not None:
        _write_variable_mappings(m_path, req.variable_mappings)
        updated_vars = len(req.variable_mappings)

    if req.value_mappings is not None:
        _write_value_mappings(v_path, req.value_mappings)
        updated_vals = len(req.value_mappings)

    return {
        "status": "success",
        "system": "OpenMRS 3.x (O3)",
        "program": prog_key,
        "updated_variable_mappings": updated_vars,
        "updated_value_mappings": updated_vals,
    }


@app.post("/api/v1/mappings/upload", tags=["Mapping Management"])
async def upload_mapping_file(
    file: UploadFile = File(...),
    target_system: str = Query(default="bahmni", description="'bahmni' or 'o3'"),
    program: str = Query(default="maternal", description="'maternal' or 'neonatal'"),
    mapping_type: str = Query(default="variable", description="'variable' (.xlsx) or 'value' (.csv)"),
):
    """
    Upload a new Variable Mapping (.xlsx) or Value Mapping (.csv) file directly to the Mediator server.
    """
    prog_key = MATERNAL_PROGRAM if program.lower().startswith("m") else NEONATAL_PROGRAM
    if target_system.lower() == "o3":
        m_path, d_path, v_path = _pick_o3_mapping_files(prog_key)
    else:
        m_path, d_path, v_path = _pick_mapping_files(prog_key)

    dest_path = m_path if mapping_type.lower().startswith("var") else v_path

    content = await file.read()
    dest_path.write_bytes(content)

    return {
        "status": "success",
        "message": f"Successfully uploaded and saved '{file.filename}' to '{dest_path.name}'",
        "target_system": target_system,
        "program": prog_key,
        "mapping_type": mapping_type,
        "file_size_bytes": len(content),
    }


# --- Status & Resumption Endpoints ---

@app.get("/api/v1/sync/status", tags=["Sync Status & Resumption"])
def get_sync_status():
    """
    Returns current sync status, progress breakdown, failed records detail, and checkpoint resumption state.
    """
    checkpoint = _load_checkpoint()
    log_file = checkpoint.get("artifacts", {}).get("import_log_csv", "")
    detailed_issues = _parse_issues_csv(Path(log_file)) if log_file else []

    return {
        "checkpoint": checkpoint,
        "failed_records_count": len(detailed_issues),
        "failed_records_detail": detailed_issues,
    }


@app.post("/api/v1/sync/resume", tags=["Sync Status & Resumption"])
def resume_sync(req: ResumeSyncRequest):
    """
    Resumes an interrupted or incomplete sync from the checkpoint transformed CSV file.
    """
    checkpoint = _load_checkpoint()
    transformed_csv = checkpoint.get("artifacts", {}).get("transformed_csv")
    if not transformed_csv or not Path(transformed_csv).is_file():
        raise HTTPException(status_code=400, detail="No valid checkpoint transformed CSV found to resume from.")

    resolved_log = checkpoint.get("artifacts", {}).get("import_log_csv") or str(Path(transformed_csv).parent / "dhis2_import_value_issues.csv")
    dhis2_url = req.dhis2_url or checkpoint.get("dhis2_url")
    dhis2_user = req.dhis2_username or checkpoint.get("dhis2_username")
    dhis2_pass = req.dhis2_password or checkpoint.get("dhis2_password")

    start_time = time.time()
    try:
        import_stats = import_rows(
            base_url=dhis2_url,
            username=dhis2_user,
            password=dhis2_pass,
            input_path=Path(transformed_csv),
            log_path=Path(resolved_log),
        )
        elapsed = round(time.time() - start_time, 2)

        checkpoint["status"] = "completed" if import_stats.get("row_errors", 0) == 0 else "completed_with_errors"
        checkpoint["dhis2_import_stats"] = import_stats
        checkpoint["can_resume"] = import_stats.get("row_errors", 0) > 0
        _save_checkpoint(checkpoint)

        return {
            "status": "resumed_success",
            "execution_time_seconds": elapsed,
            "dhis2_import_stats": import_stats,
            "checkpoint": checkpoint,
        }
    except Exception as exc:
        checkpoint["status"] = "interrupted"
        checkpoint["error"] = str(exc)
        _save_checkpoint(checkpoint)
        raise HTTPException(status_code=500, detail=f"Sync resumption failed: {exc}")


# --- Pipeline Sync Endpoints ---

@app.post("/api/v1/sync/bahmni", tags=["Pipeline Synchronization"])
def sync_bahmni(req: BahmniSyncRequest):
    """
    Executes end-to-end pipeline for OpenMRS 2.x (Bahmni):
    Extraction -> Demographic Filtering -> Transformation -> DHIS2 Tracker Import
    """
    start_time = time.time()
    work_dir = Path(req.output_dir) if req.output_dir else Path(tempfile.mkdtemp(prefix="bahmni_mediator_"))
    work_dir.mkdir(parents=True, exist_ok=True)

    checkpoint_data = {
        "status": "in_progress",
        "system": "Bahmni (OpenMRS 2.x)",
        "facility_code": req.facility_code,
        "visit_type": req.visit_type_name,
        "dhis2_url": req.dhis2_url,
        "dhis2_username": req.dhis2_username,
        "dhis2_password": req.dhis2_password,
        "work_dir": str(work_dir),
        "can_resume": False,
    }
    _save_checkpoint(checkpoint_data)

    try:
        # Step 1: Connect to OpenMRS
        base_url = normalize_base_url(req.emr_base_url)
        api = ApiClient(base_url=base_url, username=req.emr_username, password=req.emr_password)
        if not api.login_session():
            checkpoint_data["status"] = "failed_auth"
            _save_checkpoint(checkpoint_data)
            raise HTTPException(status_code=401, detail="Failed to authenticate with OpenMRS Server.")

        # Step 2: Determine Program & Date Filter
        start_date = normalize_date_filter(req.start_date)
        end_date = normalize_date_filter(req.end_date)
        validate_date_range(start_date, end_date)

        program_value = determine_program_from_visit_type(req.visit_type_name)
        if not program_value:
            checkpoint_data["status"] = "invalid_program"
            _save_checkpoint(checkpoint_data)
            raise HTTPException(status_code=400, detail=f"Cannot determine DHIS2 program for visit type '{req.visit_type_name}'.")

        checkpoint_data["program"] = program_value

        # Step 3: Fetch & Extract Patients
        visit_types = api.get_visit_types()
        matched_visit = next((v for v in visit_types if v.get("name", "").lower() == req.visit_type_name.lower()), None)
        if not matched_visit:
            checkpoint_data["status"] = "visit_type_not_found"
            _save_checkpoint(checkpoint_data)
            raise HTTPException(status_code=404, detail=f"Visit type '{req.visit_type_name}' not found on EMR server.")

        visits = api.get_visits(visit_start_date=start_date, visit_end_date=end_date, visit_type_uuid=matched_visit["uuid"])
        patients = get_patients_by_visit_type(
            visits=visits,
            visit_type_uuid=matched_visit["uuid"],
            visit_start_date=start_date,
            visit_end_date=end_date,
        )

        export_csv = work_dir / "openmrs_export.csv"
        exported_count = write_patients_csv(
            api=api,
            patients=patients,
            output_filename=export_csv,
            org_unit_code=req.facility_code,
            program_value=program_value,
            fetch_concurrency=8,
        )

        # Step 4: Transform Data
        mapping_path, dict_path, val_path = _pick_mapping_files(
            program_value,
            custom_mapping=req.mapping_file_path,
            custom_value=req.value_mapping_file_path,
        )
        set_mapping_files(mapping_path, dict_path, val_path)
        transformed_csv = work_dir / "dhis2_tracker_import.csv"
        transformed_rows, counts, missing_fields = transform_rows(export_csv, transformed_csv)

        checkpoint_data["artifacts"] = {
            "export_csv": str(export_csv),
            "transformed_csv": str(transformed_csv),
            "import_log_csv": str(work_dir / "dhis2_import_value_issues.csv"),
        }
        checkpoint_data["patients_extracted"] = len(patients)
        checkpoint_data["eligible_exported"] = exported_count
        checkpoint_data["transformed_rows"] = transformed_rows
        checkpoint_data["can_resume"] = True
        _save_checkpoint(checkpoint_data)

        # Step 5: Import to DHIS2
        resolved_log = work_dir / "dhis2_import_value_issues.csv"
        import_stats = import_rows(
            base_url=req.dhis2_url,
            username=req.dhis2_username,
            password=req.dhis2_password,
            input_path=transformed_csv,
            log_path=resolved_log,
        )

        elapsed = round(time.time() - start_time, 2)
        checkpoint_data["status"] = "completed" if import_stats.get("row_errors", 0) == 0 else "completed_with_errors"
        checkpoint_data["dhis2_import_stats"] = import_stats
        checkpoint_data["can_resume"] = import_stats.get("row_errors", 0) > 0
        _save_checkpoint(checkpoint_data)

        return {
            "status": "success",
            "execution_time_seconds": elapsed,
            "program": program_value,
            "facility_code": req.facility_code,
            "patients_extracted": len(patients),
            "eligible_records_exported": exported_count,
            "transformed_rows": transformed_rows,
            "dhis2_import_stats": import_stats,
            "artifacts": checkpoint_data["artifacts"],
        }

    except Exception as exc:
        checkpoint_data["status"] = "interrupted"
        checkpoint_data["error"] = str(exc)
        _save_checkpoint(checkpoint_data)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/v1/sync/o3", tags=["Pipeline Synchronization"])
def sync_o3(req: O3SyncRequest):
    """
    Executes end-to-end pipeline for OpenMRS 3 (O3):
    Schema Extraction -> Demographic Filtering -> O3 Transformation -> DHIS2 Tracker Import
    """
    start_time = time.time()
    work_dir = Path(req.output_dir) if req.output_dir else Path(tempfile.mkdtemp(prefix="o3_mediator_"))
    work_dir.mkdir(parents=True, exist_ok=True)

    checkpoint_data = {
        "status": "in_progress",
        "system": "OpenMRS 3.x (O3)",
        "facility_code": req.facility_code,
        "visit_type": req.visit_type_name,
        "dhis2_url": req.dhis2_url,
        "dhis2_username": req.dhis2_username,
        "dhis2_password": req.dhis2_password,
        "work_dir": str(work_dir),
        "can_resume": False,
    }
    _save_checkpoint(checkpoint_data)

    try:
        # Step 1: Connect to OpenMRS
        base_url = normalize_base_url(req.emr_base_url)
        api = ApiClient(base_url=base_url, username=req.emr_username, password=req.emr_password)
        if not api.login_session():
            checkpoint_data["status"] = "failed_auth"
            _save_checkpoint(checkpoint_data)
            raise HTTPException(status_code=401, detail="Failed to authenticate with OpenMRS Server.")

        # Step 2: Date & Program Filter
        start_date = normalize_date_filter(req.start_date)
        end_date = normalize_date_filter(req.end_date)
        validate_date_range(start_date, end_date)

        program_value = determine_o3_program(req.visit_type_name)
        if not program_value:
            checkpoint_data["status"] = "invalid_program"
            _save_checkpoint(checkpoint_data)
            raise HTTPException(status_code=400, detail=f"Cannot determine DHIS2 program for visit type '{req.visit_type_name}'.")

        checkpoint_data["program"] = program_value

        # Step 3: Fetch & Extract Patients with Form Schemas
        visit_types = api.get_visit_types()
        matched_visit = next((v for v in visit_types if v.get("name", "").lower() == req.visit_type_name.lower()), None)
        if not matched_visit:
            checkpoint_data["status"] = "visit_type_not_found"
            _save_checkpoint(checkpoint_data)
            raise HTTPException(status_code=404, detail=f"Visit type '{req.visit_type_name}' not found on EMR server.")

        visits = api.get_visits(visit_start_date=start_date, visit_end_date=end_date, visit_type_uuid=matched_visit["uuid"])
        patients = get_o3_patients(
            visits=visits,
            visit_type_uuid=matched_visit["uuid"],
            visit_start_date=start_date,
            visit_end_date=end_date,
        )

        registry = load_default_forms(O3_SCHEMA_ROOT) if O3_SCHEMA_ROOT.is_dir() else FormRegistry([])
        export_csv = work_dir / "openmrs3_export.csv"
        exported_count = write_o3_patients_csv(
            api=api,
            registry=registry,
            patients=patients,
            output_filename=export_csv,
            org_unit_code=req.facility_code,
            program_value=program_value,
            fetch_concurrency=12,
        )

        # Step 4: Transform O3 Data
        mapping_path, dict_path, val_path = _pick_o3_mapping_files(
            program_value,
            custom_mapping=req.mapping_file_path,
            custom_value=req.value_mapping_file_path,
        )
        set_mapping_files(mapping_path, dict_path, val_path)
        transformed_csv = work_dir / "dhis2_tracker_import.csv"
        transformed_rows, counts, missing_fields = transform_o3_rows(export_csv, transformed_csv)

        checkpoint_data["artifacts"] = {
            "export_csv": str(export_csv),
            "transformed_csv": str(transformed_csv),
            "import_log_csv": str(work_dir / "dhis2_import_value_issues.csv"),
        }
        checkpoint_data["patients_extracted"] = len(patients)
        checkpoint_data["eligible_exported"] = exported_count
        checkpoint_data["transformed_rows"] = transformed_rows
        checkpoint_data["can_resume"] = True
        _save_checkpoint(checkpoint_data)

        # Step 5: Import to DHIS2
        resolved_log = work_dir / "dhis2_import_value_issues.csv"
        import_stats = import_rows(
            base_url=req.dhis2_url,
            username=req.dhis2_username,
            password=req.dhis2_password,
            input_path=transformed_csv,
            log_path=resolved_log,
        )

        elapsed = round(time.time() - start_time, 2)
        checkpoint_data["status"] = "completed" if import_stats.get("row_errors", 0) == 0 else "completed_with_errors"
        checkpoint_data["dhis2_import_stats"] = import_stats
        checkpoint_data["can_resume"] = import_stats.get("row_errors", 0) > 0
        _save_checkpoint(checkpoint_data)

        return {
            "status": "success",
            "execution_time_seconds": elapsed,
            "program": program_value,
            "facility_code": req.facility_code,
            "patients_extracted": len(patients),
            "eligible_records_exported": exported_count,
            "transformed_rows": transformed_rows,
            "dhis2_import_stats": import_stats,
            "artifacts": checkpoint_data["artifacts"],
        }

    except Exception as exc:
        checkpoint_data["status"] = "interrupted"
        checkpoint_data["error"] = str(exc)
        _save_checkpoint(checkpoint_data)
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    print("Starting EMR-DHIS2 Interoperability Mediator Server on http://127.0.0.1:8000")
    print("Interactive API Docs available at: http://127.0.0.1:8000/docs")
    uvicorn.run("mediator:app", host="127.0.0.1", port=8000, reload=True)

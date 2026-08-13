from __future__ import annotations

import json
import re
import time
from typing import Dict, List, Optional, Sequence

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from o3app.config import BLANK_MARKERS
from o3app.models import ImportValueIssue, ProgramConfig
from o3app.utils import blank_to_empty, normalize_date

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _install_connection_retries(session: requests.Session) -> None:
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1.0,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD", "OPTIONS", "TRACE", "PUT", "DELETE"}),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)


EVENT_DATE_HINTS = (
    "event date",
    "admission date",
    "date and time of admission",
    "date of delivery",
    "date of birth",
    "evaluation date",
    "date of referral",
    "discharge date",
    "date of visit",
    "date form filled",
)


class Dhis2RequestError(RuntimeError):
    def __init__(self, method: str, url: str, status_code: int, payload: object) -> None:
        self.method = method
        self.url = url
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"{method} {url} failed: {payload}")


def normalize_dhis2_base_url(raw: str) -> str:
    value = str(raw or "").strip().rstrip("/")
    if not value:
        raise ValueError("DHIS2 URL is required.")
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    lower = value.lower()
    if lower.endswith("/api"):
        return value
    if "/api/" in lower:
        return value.rstrip("/")
    return f"{value}/api"


def today_date() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")


def normalize_time_value(value: str) -> str:
    match = re.search(r"(?:T|\b)(\d{1,2}:\d{2})(?::\d{2})?", str(value or ""))
    if not match:
        return ""
    hour, minute = match.group(1).split(":")
    return f"{int(hour):02d}:{minute}"


def normalize_datetime_value(value: str) -> str:
    date_value = normalize_date(value)
    time_value = normalize_time_value(value)
    if not date_value or not time_value:
        return ""
    return f"{date_value}T{time_value}:00"


def normalize_numeric_value(value: str, integer_only: bool) -> str:
    cleaned = str(value or "").strip().replace(",", "")
    if not cleaned:
        return ""
    if integer_only:
        fallback = re.sub(r"[^0-9+-]", "", cleaned)
        if not fallback or fallback in {"+", "-"}:
            return ""
        return fallback
    fallback = re.sub(r"[^0-9+-.]", "", cleaned)
    if not fallback or fallback in {"+", "-", ".", "+.", "-."}:
        return ""
    return fallback


def normalize_label(value: str) -> str:
    cleaned = (
        str(value or "")
        .replace("\u2019", "'")
        .replace("`", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .strip()
        .lower()
    )
    cleaned = re.sub(r"'s\b", "", cleaned)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    parts = []
    for part in cleaned.split():
        if len(part) > 4 and part.endswith("s") and not part.endswith("ss"):
            part = part[:-1]
        parts.append(part)
    return " ".join(parts)


def option_tokens(value: str) -> tuple[str, ...]:
    normalized = normalize_label(value)
    if not normalized:
        return ()
    return tuple(sorted(set(normalized.split())))


def split_option_parts(value: str, multi_value: bool) -> List[str]:
    text = str(value or "").strip()
    if not text:
        return []
    if not multi_value:
        return [text]
    parts = re.split(r"\s*[|;,]\s*", text)
    return [part.strip() for part in parts if part.strip()]


def parse_option_codes(
    options_text: str,
) -> tuple[Dict[str, str], Dict[str, str], List[tuple[tuple[str, ...], str]]]:
    code_map: Dict[str, str] = {}
    label_map: Dict[str, str] = {}
    token_map: List[tuple[tuple[str, ...], str]] = []

    for option in str(options_text or "").split(";"):
        option = option.strip()
        if not option:
            continue
        if ":" in option:
            code, label = option.split(":", 1)
        else:
            code = option
            label = option
        code = code.strip()
        label = label.strip()
        if not code and not label:
            continue
        canonical_code = code or label
        canonical_label = label or code
        if code:
            code_map[code.casefold()] = canonical_code
        label_map[normalize_label(canonical_code)] = canonical_code
        label_map[normalize_label(canonical_label)] = canonical_code
        token_map.append((option_tokens(canonical_label), canonical_code))

    return code_map, label_map, token_map


def normalize_boolean_token(value: str) -> Optional[str]:
    normalized = normalize_label(value)
    if normalized in {"1", "true", "t", "yes", "y"}:
        return "true"
    if normalized in {"0", "false", "f", "no", "n"}:
        return "false"
    return None


def resolve_option_code(
    part: str,
    code_map: Dict[str, str],
    label_map: Dict[str, str],
    token_map: Sequence[tuple[tuple[str, ...], str]],
) -> str:
    from difflib import SequenceMatcher

    exact_code = code_map.get(part.casefold())
    if exact_code:
        return exact_code

    normalized = normalize_label(part)
    mapped = label_map.get(normalized)
    if mapped:
        return mapped

    boolean_value = normalize_boolean_token(part)
    if boolean_value == "true" and "1" in code_map:
        return code_map["1"]
    if boolean_value == "false" and "0" in code_map:
        return code_map["0"]

    part_token_set = set(option_tokens(part))
    best_code = ""
    best_score = 0.0
    for option_token_tuple, option_code in token_map:
        option_token_set = set(option_token_tuple)
        if not option_token_set:
            continue
        if part_token_set and option_token_set.issubset(part_token_set):
            score = 0.9 + (len(option_token_set) / max(len(part_token_set), 1)) * 0.05
        else:
            score = SequenceMatcher(None, normalized, normalize_label(" ".join(option_token_tuple))).ratio()
        if score > best_score:
            best_score = score
            best_code = option_code

    return best_code if best_score >= 0.84 else ""


def looks_like_uid(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9]{10}", value or ""))


def reference_id(value: object) -> str:
    if isinstance(value, dict):
        for key in ("id", "uid", "programStage", "program", "trackedEntityInstance", "enrollment"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                return candidate
        return ""
    return str(value or "").strip()


def patient_label(row: Dict[str, str]) -> str:
    candidates = [
        "Patient",
        "patient",
        "Patient Name",
        "Full Name",
        "Name",
        "MRN",
        "Record ID",
    ]
    for column in candidates:
        value = blank_to_empty(row.get(column, ""))
        if value:
            return value
    return ""


def format_dhis2_error(exc: Exception) -> str:
    if isinstance(exc, Dhis2RequestError):
        payload = exc.payload
        if isinstance(payload, (dict, list)):
            return json.dumps(payload, ensure_ascii=True)
        return str(payload)
    return str(exc)


def invalid_value_reason(data_type: str, options_text: str) -> str:
    if options_text:
        return "Value is not a configured DHIS2 option and was discarded."
    if data_type in {"DATE", "TIME", "DATETIME"}:
        return f"Value could not be converted to DHIS2 {data_type} format and was discarded."
    if data_type in {
        "INTEGER", "INTEGER_ZERO_OR_POSITIVE", "INTEGER_POSITIVE",
        "INTEGER_NEGATIVE", "NUMBER", "PERCENTAGE", "UNIT_INTERVAL",
    }:
        return f"Value could not be converted to DHIS2 {data_type} format and was discarded."
    return "Value could not be normalized for DHIS2 and was discarded."


def add_import_value_issue(
    issues: Optional[List[ImportValueIssue]],
    row: Dict[str, str],
    config: ProgramConfig,
    stage: str,
    column: str,
    field_name: str,
    field_id: str,
    value: str,
    reason: str,
) -> None:
    if issues is None or not blank_to_empty(value):
        return
    issues.append(
        ImportValueIssue(
            record_id=blank_to_empty(row.get("Record ID", "")),
            patient=patient_label(row),
            program=config.program_label,
            stage=stage,
            column=column,
            field_name=field_name,
            field_id=field_id,
            value=blank_to_empty(value),
            reason=reason,
        )
    )


class Dhis2Client:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = normalize_dhis2_base_url(base_url)
        self.session = requests.Session()
        self.session.verify = False
        self.session.auth = (username, password)
        self.org_unit_cache: Dict[str, str] = {}
        _install_connection_retries(self.session)

    def _request(self, method: str, path: str, **kwargs) -> Dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        response = self.session.request(method, url, timeout=120, **kwargs)
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = response.text
            raise Dhis2RequestError(method, url, response.status_code, payload)
        if not response.text.strip():
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    def validate_credentials(self) -> None:
        self._request("GET", "me.json", params={"fields": "id,displayName,username"})

    def _request_retry_connection(
        self,
        method: str,
        path: str,
        *,
        verify,
        attempts: int = 3,
        backoff: float = 1.5,
        **kwargs,
    ) -> object:
        last_error: Optional[requests.ConnectionError] = None
        for attempt in range(attempts):
            try:
                return self._request(method, path, **kwargs)
            except requests.ConnectionError as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
                applied = None
                try:
                    applied = verify()
                except requests.ConnectionError:
                    applied = None
                if applied:
                    return applied
                time.sleep(backoff * (attempt + 1))
        assert last_error is not None
        raise last_error

    @staticmethod
    def _extract_import_reference(payload: Dict) -> str:
        response = payload.get("response") if isinstance(payload, dict) else None
        candidates = []
        if isinstance(response, dict):
            candidates.append(str(response.get("reference") or "").strip())
            import_summaries = response.get("importSummaries") or []
            if import_summaries:
                candidates.append(str(import_summaries[0].get("reference") or "").strip())
        import_summaries = payload.get("importSummaries") if isinstance(payload, dict) else None
        if isinstance(import_summaries, list) and import_summaries:
            candidates.append(str(import_summaries[0].get("reference") or "").strip())
        candidates.append(str(payload.get("reference") or "").strip() if isinstance(payload, dict) else "")

        for candidate in candidates:
            if looks_like_uid(candidate):
                return candidate
        return ""

    @staticmethod
    def _extract_conflicting_data_elements(error: Dhis2RequestError) -> List[str]:
        payload = error.payload if isinstance(error.payload, dict) else {}
        conflicts: List[Dict] = []

        def collect_conflicts(value: object) -> None:
            if isinstance(value, dict):
                if any(
                    key in value
                    for key in (
                        "object", "uid", "dataElement", "attribute",
                        "message", "errorMessage", "errorCode",
                    )
                ):
                    conflicts.append(value)
                nested = value.get("conflicts")
                if isinstance(nested, list):
                    conflicts.extend(item for item in nested if isinstance(item, dict))
                for key in (
                    "response", "importSummaries", "importSummary",
                    "validationReport", "validationReports",
                    "trackerTypeReport", "objectReports", "errorReports",
                ):
                    if key in value:
                        collect_conflicts(value[key])
            elif isinstance(value, list):
                for item in value:
                    collect_conflicts(item)

        collect_conflicts(payload)

        data_elements: List[str] = []
        for conflict in conflicts:
            object_id = str(
                conflict.get("object")
                or conflict.get("uid")
                or conflict.get("dataElement")
                or conflict.get("attribute")
                or ""
            ).strip()
            value_code = str(
                conflict.get("value")
                or conflict.get("message")
                or conflict.get("errorMessage")
                or conflict.get("errorCode")
                or ""
            ).strip().casefold()
            value_rejected = (
                value_code.startswith("value_not_valid")
                or "not valid" in value_code
                or "invalid" in value_code
                or "option" in value_code
                or "value_type" in value_code
            )
            if value_rejected:
                candidates = [object_id]
                candidates.extend(re.findall(r"\b[A-Za-z][A-Za-z0-9]{10}\b", value_code))
                args = conflict.get("args")
                if isinstance(args, list):
                    candidates.extend(str(item or "").strip() for item in args)
                for candidate in candidates:
                    if looks_like_uid(candidate):
                        data_elements.append(candidate)
        return data_elements

    @staticmethod
    def _extract_conflict_messages(error: Dhis2RequestError) -> List[str]:
        payload = error.payload if isinstance(error.payload, dict) else {}
        messages: List[str] = []

        def collect_conflicts(value: object) -> None:
            if isinstance(value, dict):
                conflict_value = str(value.get("value") or "").strip()
                message = str(
                    value.get("message")
                    or value.get("errorMessage")
                    or value.get("errorCode")
                    or ""
                ).strip()
                if conflict_value and (
                    "option" in conflict_value.casefold()
                    or "value" in conflict_value.casefold()
                    or "invalid" in conflict_value.casefold()
                    or "not valid" in conflict_value.casefold()
                ):
                    messages.append(conflict_value)
                elif message:
                    messages.append(message)
                nested = value.get("conflicts")
                if isinstance(nested, list):
                    for item in nested:
                        collect_conflicts(item)
                for key in (
                    "response", "importSummaries", "importSummary",
                    "validationReport", "validationReports",
                    "trackerTypeReport", "objectReports", "errorReports",
                ):
                    if key in value:
                        collect_conflicts(value[key])
            elif isinstance(value, list):
                for item in value:
                    collect_conflicts(item)

        collect_conflicts(payload)

        seen: set[str] = set()
        unique: List[str] = []
        for message in messages:
            if message and message.casefold() not in seen:
                seen.add(message.casefold())
                unique.append(message)
        return unique

    def resolve_org_unit(self, org_unit_code: str) -> str:
        code = blank_to_empty(org_unit_code)
        if not code:
            raise RuntimeError("org_unit is blank in the transformed CSV row.")
        if code in self.org_unit_cache:
            return self.org_unit_cache[code]

        if looks_like_uid(code):
            try:
                payload = self._request("GET", f"organisationUnits/{code}.json", params={"fields": "id"})
                org_unit_id = str(payload.get("id") or "").strip()
                if org_unit_id:
                    self.org_unit_cache[code] = org_unit_id
                    return org_unit_id
            except Exception:
                pass

        payload = self._request(
            "GET",
            "organisationUnits.json",
            params={
                "filter": f"code:eq:{code}",
                "fields": "id,code,name",
                "paging": "false",
            },
        )
        organisation_units = payload.get("organisationUnits") or []
        if not organisation_units:
            raise RuntimeError(f"No DHIS2 organisation unit was found for org_unit code '{code}'.")

        org_unit_id = str(organisation_units[0].get("id") or "").strip()
        if not org_unit_id:
            raise RuntimeError(f"Organisation unit lookup for '{code}' returned no UID.")

        self.org_unit_cache[code] = org_unit_id
        return org_unit_id

    def search_tracked_entity(
        self,
        record_attribute_id: str,
        record_id: str,
        tracked_entity_type: str = "",
        program_uid: str = "",
        org_unit_id: str = "",
    ) -> Optional[Dict]:
        params = {
            "ouMode": "ACCESSIBLE",
            "filter": f"{record_attribute_id}:EQ:{record_id}",
            "fields": (
                "trackedEntityInstance,orgUnit,attributes[attribute,value],"
                "enrollments[enrollment,program,status,events[event,programStage,eventDate,status,dataValues[dataElement,value]]]"
            ),
            "paging": "false",
        }
        if tracked_entity_type:
            params["trackedEntityType"] = tracked_entity_type
        if program_uid:
            params["program"] = program_uid
        if org_unit_id:
            params["ou"] = org_unit_id

        payload = self._request(
            "GET",
            "trackedEntityInstances.json",
            params=params,
        )
        instances = payload.get("trackedEntityInstances") or []
        return instances[0] if instances else None

    def _discard_conflicting_attributes(
        self,
        error: Dhis2RequestError,
        attributes: List[Dict[str, str]],
        config: ProgramConfig,
        row: Optional[Dict[str, str]] = None,
        issues: Optional[List[ImportValueIssue]] = None,
    ) -> List[Dict[str, str]]:
        conflicting_ids = set(self._extract_conflicting_data_elements(error))
        conflicting_ids.discard(config.record_id_attribute_id)
        if not conflicting_ids:
            return attributes

        attribute_fields = {field.attribute_id: field for field in config.attributes.values()}
        for item in attributes:
            attribute_id = str(item.get("attribute") or "")
            if attribute_id not in conflicting_ids or row is None:
                continue
            field = attribute_fields.get(attribute_id)
            add_import_value_issue(
                issues,
                row,
                config,
                "Tracked Entity Attributes",
                field.header if field else attribute_id,
                field.attribute_name if field else attribute_id,
                attribute_id,
                str(item.get("value") or ""),
                "DHIS2 rejected this attribute value during import, so the value was discarded and the tracked entity was retried.",
            )

        return [
            item
            for item in attributes
            if str(item.get("attribute") or "") not in conflicting_ids
        ]

    def get_tracked_entity(self, tei_id: str) -> Dict:
        return self._request(
            "GET",
            f"trackedEntityInstances/{tei_id}.json",
            params={
                "fields": (
                    "trackedEntityInstance,orgUnit,attributes[attribute,value],"
                    "enrollments[enrollment,program,status,events[event,programStage,eventDate,status,dataValues[dataElement,value]]]"
                )
            },
        )

    def create_tracked_entity(
        self,
        config: ProgramConfig,
        org_unit_id: str,
        attributes: List[Dict[str, str]],
        row: Optional[Dict[str, str]] = None,
        issues: Optional[List[ImportValueIssue]] = None,
    ) -> str:
        submitted_attributes = list(attributes)
        record_id = next(
            value["value"]
            for value in attributes
            if value["attribute"] == config.record_id_attribute_id
        )

        def _already_created() -> str:
            created = self.search_tracked_entity(
                record_attribute_id=config.record_id_attribute_id,
                record_id=record_id,
                tracked_entity_type=config.tracked_entity_type,
            )
            return str(created.get("trackedEntityInstance") or "").strip() if created else ""

        while True:
            try:
                payload = self._request_retry_connection(
                    "POST",
                    "trackedEntityInstances",
                    verify=_already_created,
                    json={
                        "trackedEntityType": config.tracked_entity_type,
                        "orgUnit": org_unit_id,
                        "attributes": submitted_attributes,
                    },
                )
                break
            except Dhis2RequestError as exc:
                filtered = self._discard_conflicting_attributes(
                    error=exc,
                    attributes=submitted_attributes,
                    config=config,
                    row=row,
                    issues=issues,
                )
                if len(filtered) == len(submitted_attributes):
                    raise
                submitted_attributes = filtered

        created_id = self._extract_import_reference(payload)
        if created_id:
            return created_id

        created = self.search_tracked_entity(
            record_attribute_id=config.record_id_attribute_id,
            record_id=record_id,
            tracked_entity_type=config.tracked_entity_type,
        )
        if not created:
            raise RuntimeError(f"Tracked entity '{record_id}' was created but could not be looked up afterwards.")
        return str(created.get("trackedEntityInstance") or "").strip()

    def update_tracked_entity(
        self,
        tei_id: str,
        config: ProgramConfig,
        org_unit_id: str,
        attributes: List[Dict[str, str]],
        row: Optional[Dict[str, str]] = None,
        issues: Optional[List[ImportValueIssue]] = None,
    ) -> None:
        submitted_attributes = list(attributes)
        while True:
            try:
                self._request(
                    "PUT",
                    f"trackedEntityInstances/{tei_id}",
                    json={
                        "trackedEntityInstance": tei_id,
                        "trackedEntityType": config.tracked_entity_type,
                        "orgUnit": org_unit_id,
                        "attributes": submitted_attributes,
                    },
                )
                return
            except Dhis2RequestError as exc:
                filtered = self._discard_conflicting_attributes(
                    error=exc,
                    attributes=submitted_attributes,
                    config=config,
                    row=row,
                    issues=issues,
                )
                if len(filtered) == len(submitted_attributes):
                    raise
                submitted_attributes = filtered

    def ensure_enrollment(
        self,
        tei: Dict,
        config: ProgramConfig,
        org_unit_id: str,
        enrollment_date: str,
    ) -> Dict:
        enrollments = tei.get("enrollments") or []
        for enrollment in enrollments:
            if reference_id(enrollment.get("program")) == config.program_uid:
                return enrollment

        def _already_enrolled() -> Optional[Dict]:
            refreshed = self.get_tracked_entity(tei["trackedEntityInstance"])
            for enrollment in refreshed.get("enrollments") or []:
                if reference_id(enrollment.get("program")) == config.program_uid:
                    return enrollment
            return None

        self._request_retry_connection(
            "POST",
            "enrollments",
            verify=_already_enrolled,
            json={
                "trackedEntityInstance": tei["trackedEntityInstance"],
                "program": config.program_uid,
                "orgUnit": org_unit_id,
                "enrollmentDate": enrollment_date,
                "incidentDate": enrollment_date,
                "status": "ACTIVE",
            },
        )

        refreshed = self.get_tracked_entity(tei["trackedEntityInstance"])
        for enrollment in refreshed.get("enrollments") or []:
            if reference_id(enrollment.get("program")) == config.program_uid:
                return enrollment
        raise RuntimeError(
            f"Enrollment for program {config.program_uid} could not be found after creation."
        )

    def upsert_event(
        self,
        tei_id: str,
        enrollment_id: str,
        org_unit_id: str,
        event_payload: Dict[str, object],
        existing_enrollment: Dict,
        program_uid: str,
        config: Optional[ProgramConfig] = None,
        row: Optional[Dict[str, str]] = None,
        issues: Optional[List[ImportValueIssue]] = None,
    ) -> bool:
        existing_events = existing_enrollment.get("events") or []
        matching_events = [
            event
            for event in existing_events
            if reference_id(event.get("programStage")) == str(event_payload["programStage"])
        ]
        existing_event = matching_events[-1] if matching_events else None

        def _event_applied() -> bool:
            current = self.get_tracked_entity(tei_id)
            for enrollment in current.get("enrollments") or []:
                if reference_id(enrollment.get("program")) == program_uid:
                    for event in enrollment.get("events") or []:
                        if reference_id(event.get("programStage")) == str(event_payload["programStage"]):
                            return True
            return False

        base_payload = {
            "program": program_uid,
            "programStage": event_payload["programStage"],
            "trackedEntityInstance": tei_id,
            "orgUnit": org_unit_id,
            "enrollment": enrollment_id,
            "eventDate": event_payload["eventDate"],
            "status": "ACTIVE",
        }
        data_values = list(event_payload["dataValues"])
        data_value_fields = event_payload.get("data_value_fields") or {}

        while data_values:
            try:
                if existing_event and existing_event.get("event"):
                    self._request(
                        "PUT",
                        f"events/{existing_event['event']}",
                        json={
                            "event": existing_event["event"],
                            **base_payload,
                            "dataValues": data_values,
                        },
                    )
                    return True

                self._request_retry_connection(
                    "POST",
                    "events",
                    verify=_event_applied,
                    json={**base_payload, "dataValues": data_values},
                )
                return True
            except Dhis2RequestError as exc:
                conflicting_ids = set(self._extract_conflicting_data_elements(exc))
                conflict_messages = self._extract_conflict_messages(exc)
                reason_detail = (
                    " ".join(conflict_messages) if conflict_messages else format_dhis2_error(exc)
                )
                if not conflicting_ids:
                    if config and row is not None:
                        for item in data_values:
                            data_element_id = str(item.get("dataElement") or "")
                            field_info = (
                                data_value_fields.get(data_element_id)
                                if isinstance(data_value_fields, dict)
                                else {}
                            ) or {}
                            add_import_value_issue(
                                issues,
                                row,
                                config,
                                str(event_payload.get("stage_name") or ""),
                                str(field_info.get("column") or data_element_id),
                                str(field_info.get("field_name") or data_element_id),
                                data_element_id,
                                str(item.get("value") or ""),
                                f"DHIS2 rejected the event but did not identify a single bad value; this value was not synced. ({reason_detail})",
                            )
                    return False
                for item in data_values:
                    data_element_id = str(item.get("dataElement") or "")
                    if data_element_id not in conflicting_ids or not config or row is None:
                        continue
                    field_info = (
                        data_value_fields.get(data_element_id)
                        if isinstance(data_value_fields, dict)
                        else {}
                    ) or {}
                    add_import_value_issue(
                        issues,
                        row,
                        config,
                        str(event_payload.get("stage_name") or ""),
                        str(field_info.get("column") or data_element_id),
                        str(field_info.get("field_name") or data_element_id),
                        data_element_id,
                        str(item.get("value") or ""),
                        f"DHIS2 rejected this value during import, so the value was discarded and the event was retried. ({reason_detail})",
                    )
                filtered = [
                    item for item in data_values if str(item.get("dataElement") or "") not in conflicting_ids
                ]
                if len(filtered) == len(data_values):
                    raise
                data_values = filtered
                continue

        return False

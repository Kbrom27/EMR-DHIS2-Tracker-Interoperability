from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import requests
import urllib3

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _install_connection_retries(session: requests.Session) -> None:
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1.0,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD", "OPTIONS", "TRACE"}),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
    session.mount("http://", adapter)
    session.mount("https://", adapter)


def normalize_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        raise ValueError("OpenMRS server/IP is required.")
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    lower = value.lower()
    if lower.endswith("/ws/rest/v1"):
        return value
    if "/ws/rest/" in lower:
        return value.rstrip("/")
    if lower.endswith("/openmrs"):
        return f"{value}/ws/rest/v1"
    return f"{value}/openmrs/ws/rest/v1"


class ApiClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.verify = False
        self.session_ok = False
        _install_connection_retries(self.session)

    def _sync_base_url_from_response(self, response: requests.Response) -> None:
        final_url = response.url
        if not final_url:
            return
        parts = urlsplit(final_url)
        path = parts.path.rstrip("/")
        marker = "/ws/rest/v1"
        lower_path = path.lower()
        marker_index = lower_path.find(marker)
        if marker_index == -1:
            return
        rest_path = path[: marker_index + len(marker)]
        self.base_url = f"{parts.scheme}://{parts.netloc}{rest_path}"

    def login_session(self) -> bool:
        url = f"{self.base_url}/session"
        response = self.session.get(url, auth=(self.username, self.password), timeout=60)
        response.raise_for_status()
        self._sync_base_url_from_response(response)
        payload = response.json()
        return bool(payload.get("authenticated"))

    def get_json(self, url: str, params: Optional[Dict[str, str]] = None) -> Dict:
        kwargs = {"params": params, "timeout": 120}
        if not self.session_ok:
            kwargs["auth"] = (self.username, self.password)
        response = self.session.get(url, **kwargs)
        response.raise_for_status()
        self._sync_base_url_from_response(response)
        return response.json()

    def get_all_results_by_params(
        self, resource: str, params: Dict[str, str], limit: int = 100
    ) -> List[Dict]:
        start_index = 0
        all_results: List[Dict] = []

        while True:
            page_params = dict(params)
            page_params["limit"] = str(limit)
            page_params["startIndex"] = str(start_index)
            url = f"{self.base_url}/{resource.lstrip('/')}"
            raw = self.get_json(url, params=page_params)
            results = raw.get("results") or []
            if not results:
                break
            all_results.extend(results)
            if len(results) < limit:
                break
            start_index += limit

        return all_results

    def get_all_results(self, path: str, limit: int = 100) -> List[Dict]:
        start_index = 0
        all_results: List[Dict] = []
        separator = "&" if "?" in path else "?"

        while True:
            url = f"{self.base_url}/{path}{separator}limit={limit}&startIndex={start_index}"
            raw = self.get_json(url)
            results = raw.get("results") or []
            if not results:
                break
            all_results.extend(results)
            if len(results) < limit:
                break
            start_index += limit

        return all_results

    def get_visit_types(self) -> List[Dict]:
        return self.get_all_results("visittype?v=default")

    def get_visits(
        self,
        visit_start_date: Optional[str] = None,
        visit_end_date: Optional[str] = None,
        page_size: int = 100,
        visit_type_uuid: Optional[str] = None,
    ) -> List[Dict]:
        params = {
            "includeInactive": "true",
            "v": "custom:(uuid,startDatetime,patient:(uuid,display),visitType:(uuid,name))",
        }
        if visit_start_date:
            params["fromStartDate"] = f"{visit_start_date}T00:00:00.000Z"
        if visit_end_date:
            params["toStartDate"] = f"{visit_end_date}T23:59:59.999Z"

        if visit_type_uuid:
            params_with_type = dict(params)
            params_with_type["visitType"] = visit_type_uuid
            try:
                return self.get_all_results_by_params("visit", params=params_with_type, limit=page_size)
            except Exception:
                # OpenMRS REST API on this server does not support `visitType` filter on /visit resource; fallback
                pass

        return self.get_all_results_by_params("visit", params=params, limit=page_size)

    def get_patient_person(self, patient_uuid: str) -> Dict:
        person_url = urljoin(self.base_url + "/", f"person/{patient_uuid}")
        return self.get_json(
            person_url,
            params={
                "v": (
                    "custom:(age,gender,birthdate,deathDate,causeOfDeath,"
                    "preferredName:(givenName,familyName),"
                    "preferredAddress:(address1,address2,address3,cityVillage,stateProvince,countyDistrict),"
                    "auditInfo:(dateCreated),attributes:(attributeType:(display),value))"
                )
            },
        )

    def get_patient_obs(self, patient_uuid: str) -> List[Dict]:
        preferred_paths = [
            (
                f"obs?patient={patient_uuid}"
                "&v=custom:(uuid,display,obsDatetime,concept:(name,display),value,"
                "encounter:(encounterDatetime,encounterType:(display),form:(name,display,uuid)),"
                "groupMembers:(uuid,display,obsDatetime,concept:(name,display),value,groupMembers:(uuid,display,obsDatetime,concept:(name,display),value,groupMembers:(uuid,display,obsDatetime,concept:(name,display),value,groupMembers:(uuid,display,obsDatetime,concept:(name,display),value)))))"
            ),
            (
                f"obs?patient={patient_uuid}"
                "&v=custom:(uuid,display,obsDatetime,concept:(name,display),value,"
                "encounter:(encounterDatetime,encounterType:(display)),"
                "groupMembers:(uuid,display,obsDatetime,concept:(name,display),value,groupMembers:(uuid,display,obsDatetime,concept:(name,display),value,groupMembers:(uuid,display,obsDatetime,concept:(name,display),value,groupMembers:(uuid,display,obsDatetime,concept:(name,display),value)))))"
            ),
            (
                f"obs?patient={patient_uuid}"
                "&v=custom:(uuid,display,obsDatetime,concept:(name,display),value,groupMembers:(uuid,display,obsDatetime,concept:(name,display),value,groupMembers:(uuid,display,obsDatetime,concept:(name,display),value,groupMembers:(uuid,display,obsDatetime,concept:(name,display),value))))"
            ),
        ]
        for path in preferred_paths:
            try:
                return self.get_all_results(path)
            except requests.HTTPError:
                continue
        return []

    def get_patient_orders(self, patient_uuid: str) -> List[Dict]:
        preferred_paths = [
            (
                f"order?patient={patient_uuid}"
                "&v=custom:(display,action,instructions,commentToFulfiller,dateActivated,dateCreated,"
                "orderType:(display),type,concept:(display),drug:(display),dose,"
                "doseUnits:(display),frequency:(display),duration,durationUnits:(display),"
                "quantity,route:(display))"
            ),
            (
                f"order?patient={patient_uuid}"
                "&v=custom:(display,dateActivated,dateCreated,orderType:(display),concept:(display),drug:(display))"
            ),
            f"order?patient={patient_uuid}&v=default",
        ]
        for path in preferred_paths:
            try:
                return self.get_all_results(path)
            except requests.HTTPError:
                continue
        return []

    def get_patient_encounters(self, patient_uuid: str) -> List[Dict]:
        preferred_paths = [
            (
                f"encounter?patient={patient_uuid}"
                "&v=custom:(encounterDatetime,encounterType:(display),form:(name,display,uuid),"
                "obs:(uuid,display,obsDatetime,concept:(name,display),value,groupMembers:(uuid,display,obsDatetime,concept:(name,display),value,groupMembers:(uuid,display,obsDatetime,concept:(name,display),value,groupMembers:(uuid,display,obsDatetime,concept:(name,display),value)))),"
                "orders:(display,action,instructions,commentToFulfiller,dateActivated,dateCreated,orderType:(display),type,"
                "concept:(display),drug:(display),dose,doseUnits:(display),frequency:(display),"
                "duration,durationUnits:(display),quantity,route:(display)))"
            ),
            (
                f"encounter?patient={patient_uuid}"
                "&v=custom:(encounterDatetime,encounterType:(display),"
                "obs:(uuid,display,obsDatetime,concept:(name,display),value,groupMembers:(uuid,display,obsDatetime,concept:(name,display),value,groupMembers:(uuid,display,obsDatetime,concept:(name,display),value))),"
                "orders:(display,dateActivated,dateCreated,orderType:(display),concept:(display),drug:(display)))"
            ),
        ]
        for path in preferred_paths:
            try:
                return self.get_all_results(path)
            except requests.HTTPError:
                continue
        return []

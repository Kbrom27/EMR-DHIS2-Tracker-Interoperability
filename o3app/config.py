from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

RESOURCES_DIR = Path(__file__).resolve().parents[1] / "Resources"

O3_SCHEMA_ROOT = RESOURCES_DIR / "O3" / "Schemas"
O3_METADATA_PATH = RESOURCES_DIR / "O3" / "metadata_for_openmrs_3x.json"

MATERNAL_PROGRAM = "Maternal Inpatient Data/aLoraiFNkng"
NEONATAL_PROGRAM = "Neonatal Care Form/QYJKpoUeg9F"
PROGRAM_LABELS = (MATERNAL_PROGRAM, NEONATAL_PROGRAM)

SPECIAL_COLUMNS = ["org_unit", "program", "Record ID"]
CONTEXT_COLUMNS = ["visit_date"]
HEADER_SEPARATOR = " :: "
BLANK_MARKERS = {"", "none", "null", "nan", "n/a"}
STOPWORDS = {"a", "an", "at", "for", "in", "n", "of", "on", "the", "to"}

DETAIL_COLUMNS = [
    "patient_uuid",
    "patient_display",
    "patient_id",
    "org_unit",
    "Record ID",
    "program",
    "first_name",
    "family_name",
    "age",
    "gender",
    "birth_date",
    "death_date",
    "cause_of_death",
    "address1",
    "address2",
    "address3",
    "city_village",
    "state_province",
    "county_district",
    "registration_date",
    "name_in_local_language",
    "family_name_local",
    "middle_name_local",
    "caste",
    "class",
    "education_details",
    "occupation",
    "primary_contact",
    "secondary_contact",
    "fathers_husbands_name",
    "secondary_identifier",
    "land_holding_acres",
    "debt_rs",
    "distance_from_center_km",
    "urban",
    "cluster",
    "ration_card_type",
    "family_income_per_month_rs",
    "email_address",
    "payment_method",
    "cbhi_id",
    "expiry_date",
    "visit_date",
    "diagnoses",
    "lab_results",
    "orders",
    "medications",
]

DEFAULT_PROGRAM_SPECS = {
    MATERNAL_PROGRAM: {
        "mapping_path": RESOURCES_DIR / "EMR-DHIS2 Tracker Maternal Mapping.xlsx",
        "dictionary_path": RESOURCES_DIR / "MID data disctionary.xlsx",
    },
    NEONATAL_PROGRAM: {
        "mapping_path": RESOURCES_DIR / "EMR-DHIS2 Tracker Neonatal Mapping.xlsx",
        "dictionary_path": RESOURCES_DIR / "NCF data disctionary.xlsx",
    },
}

PROGRAM_SPECS: Dict[str, Dict[str, Path]] = dict(DEFAULT_PROGRAM_SPECS)

FACILITIES = (
    ('Adama Teaching Hospital', 'ADMT'),
    ('Olenchity Primary Hospital', 'OLC'),
    ('Meki Primary Hospital', 'MKP'),
    ('Batu Primary Hospital', 'BT'),
    ('Adare GH', 'ADR'),
    ('Tula Primary Hospital', 'TUL'),
    ('Karamara Primary Hospital', 'KRM'),
    ('Dubti General Hospital', 'DUB'),
    ('Axum referral hospital', 'AxRH'),
    ('Mulu Assefa Primary hospital', 'MASPH'),
    ('Boru Meda GH', 'BRM'),
    ('Debre Birhan CSH', 'DBR'),
    ('Test', 'Test'),
    ('Abiadi General hospital', 'ABD'),
    ('Abomsa Primary Hospital', 'ABM'),
    ('Addis Alem  PH', 'AAL'),
    ('Addis Zemen  PH', 'AZ'),
    ('Adet PH', 'ADT'),
    ('Adet Primary Hospital', 'ADTPH'),
    ('Adi Daero Primary Hospital', 'ADDRPH'),
    ('Adigrat general hospital', 'AGH'),
    ('Adigudom primary hospital', 'APH'),
    ('Adishu primary hospital', 'AsPH'),
    ('Adola General Hospital', 'ADLGH'),
    ('Adwa general hospital', 'AwGH'),
    ('Agaro General Hospital', 'AGR'),
    ('Alemketema General Hospital', 'ALKTGH'),
    ('Aleta Wondo PH', 'ALW'),
    ('Amaro Kele Primary Hospital', 'AMRKPH'),
    ('Amaya Primary Hospital', 'AMYPH'),
    ('Ambo  General Hospital', 'AMG'),
    ('Ambo   university   Hospital', 'AMU'),
    ('Amdework Primary Hospital', 'AMDWPH'),
    ('Ameya Primary Hospital', 'AMY'),
    ('Andabet Primary Hospital', 'ADBTPH'),
    ('Angacha Primary Hospital', 'ANGPH'),
    ('Arba Minch General Hospital', 'AMC'),
    ('Arero Primary Hospital', 'ARRPH'),
    ('Arjo Primary Hospital', 'ARJPH'),
    ('Arsi Negelle Primary Hospital', 'ARN'),
    ('Asabot Primary Hospital', 'ASB'),
    ('Asela Teaching Hospital', 'ASLT'),
    ('Ataye Primary Hospital', 'ATYPH'),
    ('Ayder referral hospital', 'ARH'),
    ('Ayekel Primary Hospital', 'AYKPH'),
    ('Ayssaita Primary Hospital', 'ASYT'),
    ('Badessa Primary Hospital', 'BDSPH'),
    ('Bako Primary Hospital', 'BKPH'),
    ('Balegazigar General Hospital', 'BLGZGH'),
    ('Bedelle General Hospital', 'BDLGH'),
    ('Bedeno Primary Hospital', 'BDN'),
    ('Belegesgar Primary Hospital', 'BLG'),
    ('Bele Primary Hospital', 'BLPH'),
    ('Bichena Primary Hospital', 'BCNPH'),
    ('Birshwa Primary Hospital', 'BRSPH'),
    ('Bisdemo General Hospital', 'BSDMGH'),
    ('Bishoftu General Hospital', 'BSFGH'),
    ('Bisidimo General Hospital', 'BSD'),
    ('Bokoji Primary Hospital', 'BKJ'),
    ('Bona GH', 'BN'),
    ('Bore Primary Hospital', 'BRPH'),
    ('Boru meda GH', 'BRM'),
    ('Bue Primary Hospital', 'BPH'),
    ('Bule Hora General Hospital', 'BLHRGH'),
    ('Burie  PH', 'BR'),
    ('Butajira GH', 'BTJ'),
    ('Chagni PH', 'CHG'),
    ('Chalenko Primary Hospital', 'CHLKPH'),
    ('Chancho General Hospital', 'CHNGH'),
    ('Chancho Primary Hospital', 'CHN'),
    ('Chiro General Hospital', 'CHR'),
    ('Chora Primary Hospital', 'CHRPH'),
    ('Ciro General Hospital', 'CRGH'),
    ('Dangela  PH', 'DNG'),
    ('Daye GH', 'DAY'),
    ('Debark General Hospital', 'DBRKGH'),
    ('Debere Markos referral hospital', 'DMK'),
    ('Debrebirhan CSH', 'DBR'),
    ('Debresina Primary Hospital', 'DSN'),
    ('Debre Tabor referral Hospital', 'DBT'),
    ('Debre Tabor referral Hospital 2', 'DBT2'),
    ('Debrwork Primary Hospital', 'DBRWPH'),
    ('Dedar General Hospital', 'DDR'),
    ('Deder General Hospital', 'DDRGH'),
    ('Dedo Primary Hospital', 'DDO'),
    ('Dejen PH', 'DJN'),
    ('Delgi Primary Hospital', 'DLGPH'),
    ('Delomena General Hospital', 'DLMGH'),
    ('Deneba Primary Hospital', 'DNB'),
    ('Dessie CSH', 'DSE'),
    ('Dichoto Health center', 'DCHT'),
    ('Didesa Primary Hospital', 'DDSPH'),
    ('Dilla Referral Hospital', 'DLA'),
    ('Dimtu Primary Hospital', 'DMTPH'),
    ('Dodola Primary Hospital', 'DDL'),
    ('Dore Bafano Primary hospital', 'DOR'),
    ('Doyogena Primary Hospital', 'DYGPH'),
    ('Dr Ambachew Primary Hospital', 'DAMBPH'),
    ('Dr.Bogalech Memorial GH', 'DBGM'),
    ('Dr Tsegay G/her primary hospital', 'DTPH'),
    ('Durbetie  PH', 'DRB'),
    ('Ebinate Primary Hospital', 'EBNPH'),
    ('Edaga-arbi primary hospital', 'EaPH'),
    ('Endabaguna primary hospital', 'EgPH'),
    ('Ethiopia', 'ET'),
    ('Felegehiwot Referral', 'FLG'),
    ('Fenote selam  General Hospital', 'FNS'),
    ('Feresebet  PH', 'FRS'),
    ('Fiche comprehensive specialized Referral Hospital', 'FCSRH'),
    ('FINC Abiadi General Hospital', 'FABD'),
    ('FINC Adigrat General Hospital', 'FADG'),
    ('FINC Adigudem Primary Hospital', 'FADGD'),
    ('FINC  Adwa General Hospital', 'FADW'),
    ('FINC Aksum Referral Hospital', 'FAKS'),
    ('FINC Ayder Referral Hospital', 'FAYD'),
    ('FINC Fresemaetat Primary Hospital', 'FFRS'),
    ('FINC Hagereselam Primary Hospital', 'FHGS'),
    ('FINC Lemlem Karl General Hospital', 'FLKR'),
    ('FINC Mekelle General Hospital', 'FMKL'),
    ('FINC Quiha Primary Hospital', 'FQHA'),
    ('FINC St.Marry General Hospital', 'FSTM'),
    ('FINC Tigray', 'FTIG'),
    ('FINC Wukro Maray Primary Hospital', 'FWKM'),
    ('FINC Wukro Primary Hospital', 'FWKR'),
    ('Fitche General Hospital', 'FCH'),
    ('Freweyni primary hospital', 'FRW'),
    ('Galamso General Hospital', 'GLM'),
    ('Galemiso General Hospital', 'GLMSGH'),
    ('Gambo General Hospital', 'GMBGH'),
    ('Garamuleta General Hospital', 'GRMLGH'),
    ('Gara Muleta General Hospital', 'GRM'),
    ('Gazer Primary Hospital', 'GZRPH'),
    ('Gebre Tsadik shawo GH', 'GTS'),
    ('Gedeb Primary Hospital', 'GDBPH'),
    ('Gedo General Hospital', 'GD'),
    ('Gimbichu Primary Hospital', 'GMBPH'),
    ('Gimbi General Hospital', 'GIMBGH'),
    ('Ginchi Primary Hospital', 'GNC'),
    ('Ginnir General Hospital', 'GNRGH'),
    ('Gobessa Primary Hospital', 'GBS'),
    ('Gobo university Referral Hospital', 'GBURH'),
    ('Goro Primary Hospital', 'GRPH'),
    ('Guder Primary Hospital', 'GDR'),
    ('Gursum Primary Hospital', 'GRSMPH'),
    ('Habiru PH', 'HRB'),
    ('Hagereselam primary hospital', 'HsPH'),
    ('Haik PH', 'HIK'),
    ('Halaba  GH', 'HLB'),
    ('Hantate Primary Hospital', 'HNTPH'),
    ('Haramaya General Hospital', 'HRMYGH'),
    ('Haromaya General Hospital', 'HRM'),
    ('Harsis Health Center', 'HRS'),
    ('Hawassa U/CSH', 'HWS'),
    ('Hawzen primary hospital', 'HzPH'),
    ('Hirna Primary Hospital', 'HRNPH'),
    ('Holeta Primary Hospital', 'HLTPH'),
    ('Homacho Primary Hospital', 'HMCHPH'),
    ('Hula Primary Hospital', 'HLPH'),
    ('Huruta Primary Hospital', 'HRT'),
    ('Inchini Primary Hospital', 'INC'),
    ('Inchin Primary Hospital', 'INCPH'),
    ('Injibara General Hospital', 'IJB'),
    ('Jama Primary Hospital', 'JMPH'),
    ('Jawi PH', 'JW'),
    ('Jimma University Hospital', 'JMA'),
    ('Jinka General Hospital', 'JNK'),
    ('Kamba Primary Hospital', 'KMBPH'),
    ('Karamara General Hospital', 'KRM'),
    ('Karat Primary Hospital', 'KRTPH'),
    ('Kebado Primary Hospital', 'KBDPH'),
    ('Kellala Primary Hospital', 'KLLPH'),
    ('Kemessie General Hospital', 'KMSGH'),
    ('Kercha Primary Hospital', 'KRCHPH'),
    ('Kersa Primary Hospital', 'KRS'),
    ('Koa diba Primary Hospital', 'KDBPH'),
    ('Kobo PH', 'KBO'),
    ('Kuyu General Hospital', 'KYU'),
    ('Lalibela General Hospital', 'LBLGH'),
    ('Laska Primary Hospital', 'LSKPH'),
    ('Lekatit 11 Primary Hospital', 'LKTPH'),
    ('Leku General Hospital', 'LKG'),
    ('Leku PH', 'LK'),
    ('Leman Primary Hospital', 'LMN'),
    ('Lemlem Karl general hospital', 'LKGH'),
    ('Liben Primary Hospital', 'LBNPH'),
    ('Limugenat General Hospital', 'LMGGH'),
    ('Logia Health Center', 'LOG'),
    ('Loke Ada Primary Hospital', 'LKA'),
    ('Lumamie PH', 'LMM'),
    ('Magenta Health Center', 'MGNT'),
    ('Mayani General Hospital', 'MYNGH'),
    ('Medawalabu Primary Hospital', 'MDWPH'),
    ('Mega Primary Hospital', 'MGPH'),
    ('Mehale amba Primary Hospital', 'MHAMPH'),
    ('Mekaneysus Primary Hospital', 'MKYPH'),
    ('Mekanselam General Hospital', 'MKNSGH'),
    ('Mekelle General Hospital', 'MKG'),
    ('Mekoni primary hospital', 'MPH'),
    ('Meles primary hospital', 'MelPH'),
    ('Melka Oda General Hospital', 'MOD'),
    ('Merawi  PH', 'MRW'),
    ('Meressa Primary Hospital', 'MRS'),
    ('Mertolemariam Primary Hospital', 'MRTLPH'),
    ('Metema Primary Hospital', 'MTMPH'),
    ('Metukarl Referral Hospital', 'MTKRH'),
    ('Midega Tola Primary Hospital', 'MDGT'),
    ('Mille Health Center', 'MIL'),
    ('Mizan tepi teacing Hospital', 'MTP'),
    ('Mojo Primary Hospital', 'MJPH'),
    ('Moyale Primary Hospital', 'MYLPH'),
    ('Mudulla Primary Hospital', 'MDLPH'),
    ('MukaTuri Primary Hospital', 'MKTRPH'),
    ('Mulu Assefa Primary Hospital', 'MASPH'),
    ('Nekemte  comprehensive specialized General Hospital', 'NKCSGH'),
    ('Nifasmewecha Primary Hospital', 'NFSMPH'),
    ('Nigist Eleni M/M/referral Hospital', 'NGE'),
    ('Oda Primary Hospital', 'ODPH'),
    ('Queen Zewuditu Primary Hospital', 'QZDPH'),
    ('Quiha general hospital', 'QGH'),
    ('Qunite Primary Hospital', 'QNTPH'),
    ('Robe Didea General Hospital', 'RDD'),
    ('Robe Didea Primary Hospital', 'RBD'),
    ('Robe General Hospital', 'RBGH'),
    ('Saja General Hospital', 'SJGH'),
    ('Samre Primary Hospital', 'SMRPH'),
    ('Sandafa Primary Hospital', 'SDFPH'),
    ('Sawla General General Hospital', 'SWL'),
    ('Sayint Primary Hospital', 'SNTPH'),
    ('Seka Primary Hospital', 'SKA'),
    ('Selekleka primary hospital', 'SPH'),
    ('Semema Primary Hospital', 'SMMPH'),
    ('Semera Health Center', 'SMR'),
    ('Setema Primary Hospital', 'STMPH'),
    ('Shahura Primary Hospital', 'SHRPH'),
    ('Shashamane specialized Comprehensive Hospital', 'SHM'),
    ('Shedeho meket Primary Hospital', 'SDHMPH'),
    ('Shegaw Motta General Hospital', 'SGMTGH'),
    ('Shenan Gibe General Hospital', 'SHG'),
    ('Sheno Primary Hospital', 'SHNPH'),
    ('Shewarobit Primary Hospital', 'SHWRPH'),
    ('Simbeleta Health center', 'SMBL'),
    ('Sire Primary Hospital', 'SRPH'),
    ('SLLAA Adigudem Primary Hospital', 'SLLAA_ADG'),
    ('SLLAA Ayder Referral Hospital', 'SLLAA_Ayd'),
    ('SLLAA Hagereselam Primary Hospital', 'SLLAA_HGS'),
    ('SLLAA Mekelle General Hospital', 'SLLAA_MGH'),
    ('SLL Assist AI', 'SLLAA'),
    ('St Lukas General Hospital', 'STLKGH'),
    ('St. mary general hospital', 'StMGH'),
    ('Sude Primary Hospital', 'SDPH'),
    ('Suhul2 general hospital', 'SGH'),
    ('Suhul general hospital', 'ShGH'),
    ('Taltele Primary Hospital', 'TLTPH'),
    ('Tefera Hailu General Hospital', 'TFRHGH'),
    ('Tenta Primary Hospital', 'TNTPH'),
    ('Tepi General Hospital', 'TP'),
    ('Tercha General Hospital', 'TRC'),
    ('Test2', 'Test2'),
    ('Tibebe Ghion Referral', 'TBG'),
    ('Tora Primary Hospital', 'TRPH'),
    ('Tulubolo Primary Hospital', 'TLBPH'),
    ('University of Gondar Referral Hospital', 'UOGRH'),
    ('Uraga Primary Hospital', 'URGPH'),
    ('Wadela Primary Hospital', 'WDLPH'),
    ('Waliso General Hospital', 'WLSGH'),
    ('Welkite University Specialized Teaching Hospital', 'WLK'),
    ('Wogdi Primary Hospital', 'WGDPH'),
    ('Wogera Primary Hospital', 'WGRPH'),
    ('Wolayta dsodo T and R Hospital', 'WSD'),
    ('Woldia CSH', 'WLD'),
    ('Wollega University Referral Hospital', 'WLGURH'),
    ('Worabe CSH', 'WRB'),
    ('Wotera Primary Hospital', 'WRTPH'),
    ('Wukro general hospital', 'WGH'),
    ('Wukromaray primary hospital', 'WmPH'),
    ('Yabelo General Hospital', 'YBLGH'),
    ('Yechila primary hospital', 'YPH'),
    ('Yirba Primary Hospital', 'YRBPH'),
    ('Yirga chefe Primary Hospital', 'YRCFPH'),
    ('Yirgalem GH', 'YRG'),
)

FACILITY_CODES = dict(FACILITIES)

STAGE_NAME_ALIASES = {
    "Medication sheet": "Medication and intervention",
    "Neonatal Medication Adminstration Sheet": "Medication and intervention",
    "Neonatal Intervention Sheet": "Medication and intervention",
    "Neonatal Discharge care form": "Discharge care form",
    "Neonatal Nurse followup Sheet": "Nurse followup Sheet",
}


def normalize_stage_name(name: str) -> str:
    cleaned = str(name or "").strip()
    return STAGE_NAME_ALIASES.get(cleaned, cleaned)

MATERNAL_COMPUTED_DIAGNOSIS_HEADERS = (
    "Diagnosis :: Obstetric complications",
    "Diagnosis :: Amniotic fluid abnormalities",
    "Diagnosis :: Obstetric complications Others",
)

DIAGNOSIS_OBSTETRIC_COMPLICATIONS_HEADER = "Diagnosis :: Obstetric complications"
DIAGNOSIS_AMNIOTIC_FLUID_HEADER = "Diagnosis :: Amniotic fluid abnormalities"
DIAGNOSIS_OBSTETRIC_COMPLICATIONS_OTHER_HEADER = "Diagnosis :: Obstetric complications Others"

MATERNAL_DIAGNOSIS_SOURCE_HEADERS = ("diagnoses",)

DIAGNOSIS_METADATA_VALUES = {
    "primary", "secondary", "confirmed", "presumed", "false", "true",
}


def normalize_program_value(value: str) -> str:
    cleaned = " ".join(str(value or "").strip().split())
    lower = cleaned.casefold()
    if "aloraifnkng" in lower or "maternal inpatient data" in lower:
        return MATERNAL_PROGRAM
    if "qyjkpoueg9f" in lower or "neonatal care form" in lower:
        return NEONATAL_PROGRAM
    return cleaned


def is_patient_eligible_for_program(gender: str, age_val: Optional[Any], program_value: str) -> bool:
    import re
    norm_program = normalize_program_value(program_value)
    norm_gender = str(gender or "").strip().casefold()

    parsed_age: Optional[float] = None
    if age_val is not None:
        cleaned_age = str(age_val).strip()
        match = re.search(r"^\d+(\.\d+)?", cleaned_age)
        if match:
            try:
                parsed_age = float(match.group(0))
            except ValueError:
                parsed_age = None

    if norm_program == MATERNAL_PROGRAM:
        if norm_gender not in ("f", "female"):
            return False
        if parsed_age is not None and parsed_age < 10:
            return False
        return True

    if norm_program == NEONATAL_PROGRAM:
        if parsed_age is not None and parsed_age > 0:
            return False
        return True

    return True

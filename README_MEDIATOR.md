# EMR to DHIS2 Tracker Interoperability Mediator

A lightweight, server-side **FastAPI REST Mediator microservice** that connects **OpenMRS (Bahmni & O3)** to **DHIS2 Tracker**.

It automates the entire end-to-end pipeline:
`EMR Patient Extraction` ➔ `Demographic Eligibility Filtering` ➔ `Data Mapping & Transformation` ➔ `DHIS2 Tracker API Import`.

---

## 🚀 1. How to Run Locally

Start the mediator microservice on your local computer:

```bash
python mediator.py
```

Output:
```text
Starting EMR-DHIS2 Interoperability Mediator Server on http://127.0.0.1:8000
Interactive API Docs available at: http://127.0.0.1:8000/docs
```

---

## 🧪 2. How to Test End-to-End locally

### **Option A: Interactive Swagger Web UI (Easiest)**
1. Open your browser and go to: **[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)**
2. Click on **`POST /api/v1/sync/bahmni`** or **`POST /api/v1/sync/o3`**.
3. Click **Try it out**.
4. Fill in your test credentials and click **Execute**:
   ```json
   {
     "emr_base_url": "http://192.168.1.100:8080/openmrs",
     "emr_username": "admin",
     "emr_password": "Admin123",
     "facility_code": "1001",
     "visit_type_name": "Labour",
     "start_date": "2026-01-01",
     "end_date": "2026-08-30",
     "dhis2_url": "https://imnid.mohdigitalhealth.gov.et",
     "dhis2_username": "your_dhis_user",
     "dhis2_password": "your_dhis_password"
   }
   ```

### **Option B: Using cURL or Postman**
```bash
curl -X POST "http://127.0.0.1:8000/api/v1/sync/bahmni" \
     -H "Content-Type: application/json" \
     -d '{
           "emr_base_url": "http://192.168.1.100:8080/openmrs",
           "emr_username": "admin",
           "emr_password": "Admin123",
           "facility_code": "1001",
           "visit_type_name": "Labour",
           "start_date": "2026-01-01",
           "end_date": "2026-08-30",
           "dhis2_url": "https://imnid.mohdigitalhealth.gov.et",
           "dhis2_username": "your_dhis_user",
           "dhis2_password": "your_dhis_password"
         }'
```

---

## 🌐 3. How to Host Later (Deployment)

### **Method 1: Run as a Linux Server Background Service (Systemd)**
Create `/etc/systemd/system/emr-dhis2-mediator.service`:
```ini
[Unit]
Description=EMR-DHIS2 Interoperability Mediator
After=network.target

[Service]
User=root
WorkingDirectory=/path/to/EMR-DHIS2-Tracker-Interoperability
ExecStart=/usr/bin/python3 mediator.py
Restart=always

[Install]
WantedBy=multi-user.target
```
Enable & start service:
```bash
sudo systemctl enable --now emr-dhis2-mediator
```

### **Method 2: Docker Container**
Build & run Docker image:
```bash
docker build -t emr-dhis2-mediator .
docker run -d -p 8000:8000 --name mediator emr-dhis2-mediator
```

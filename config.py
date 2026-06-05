import os
from datetime import datetime, timedelta
import pytz

# Paths
BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_DIR = os.path.join(BASE_DIR, "credentials")
TOKEN_PATH      = os.path.join(CREDENTIALS_DIR, "ps_tender_token.json")
CREDENTIALS_FILE = os.path.join(CREDENTIALS_DIR, "credentials.json")

os.makedirs(CREDENTIALS_DIR, exist_ok=True)

# Google Sheets
SCOPES           = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
TARGET_FOLDER_ID = "1sFREkEsaedTc1voiO7QYwwTJtbtd7tHc"
SHEET_NAME       = "PS Tender Tracker"

# FTS API
FTS_API_BASE = "https://www.find-tender.service.gov.uk/api/1.0"
FTS_API_KEY  = None   # Set to your CDP-Api-Key string if you have one, else leave None
PORTAL_URL   = "https://www.find-tender.service.gov.uk/Notice"
PORTAL_NAME  = "Find-A-Tender"

CPV_CODES = [
    "48000000",  # Software packages and information systems
    "72000000",  # IT services (parent)
    "72100000",  # Hardware
    "72200000",  # Software
    "72300000",  # Data processing
    "72400000",  # Internet services
    "72500000",  # Computer-related services
    "72600000",  # Computer support and consultancy
    "72700000",  # Computer network services
    "72800000",  # Computer audit and testing services
    "72900000",  # Miscellaneous computer services
]

SUITABLE_FOR_SMES    = True
EXCLUDED_STATUSES    = ['complete']
INCLUDED_COUNTRIES   = ['United Kingdom']

KEYWORDS = [
    "Artificial Intelligence", "GenAI", "LLM", "Agentic Architecture", "Machine Learning", "MLOps",
    "Robotic Process Automation", "RPA", "Responsible AI",
    "Data Integration", "Data Governance", "Data Architecture", "Master Data Management", "MDM",
    "Data Quality", "Data Wellness", "Geospatial Analytics",
    "Cloud Migration", "Architecture Modernisation", "Hybrid Cloud", "Azure", "AWS", "GCP", "DevSecOps", "SaaS", "iSaaS",
    "Boomi", "MuleSoft", "Talend", "API Management", "Middleware",
    "Proof of Value", "PoV", "Rapid Prototyping", "Discovery Phase", "Valuepath", "Agile Delivery", "De-risking",
    "SME", "Ethnic Minority Business", "EMB", "Social Value", "Net Zero", "Living Wage", "Disability Confident",
    "S1000D", "Defence", "Aerospace", "Healthcare", "Data", "Digital", "Personalisation",
    "Clinical Information", "Public Sector Transformation",
]

# Date logic (UK timezone)
UK_TIMEZONE = pytz.timezone('Europe/London')


def get_publication_date_range():
    """
    Publication window start date, rounded to the Monday of the previous week
    when today is Wednesday or later (to avoid a mid-week partial window).
    On Monday or Tuesday, go back exactly 7 days.
    """
    today       = datetime.now(UK_TIMEZONE).date()
    day_of_week = today.weekday()   # 0 = Monday … 6 = Sunday

    if day_of_week >= 2:            # Wed, Thu, Fri, Sat, Sun
        # Go back to the Monday of the PREVIOUS week
        publication_start = today - timedelta(days=day_of_week + 7)
    else:                           # Mon, Tue
        publication_start = today - timedelta(days=7)
   
    #Temporarily relax the publication date filter to capture more tenders for testing    
    publication_start = today - timedelta(days=5)
    return publication_start, today


def get_due_date_range():
    """Returns due_start: tenders closing in less than 2 days from today are excluded."""
    today = datetime.now(UK_TIMEZONE).date()
    return today + timedelta(days=5)


# Dataset fields — canonical column order for Google Sheets
DATASET_FIELDS = [
    "Portal Name",
    "Adapter",
    "Direct URL",
    "Published On",
    "ID",
    "OCID",
    "Name",
    "Tender Due Date",
    "Clarification Due Date",
    "Procurement Stage",
    "Total Contract Value",
    "Contract Duration",
    "Annual Contract Value",
    "Tender Description",
    "Buyer Name",
    "CPV Code",
    "CPV Description",
    "SC_Flag",
    "Country",
    "Locality",
    "Suitable for SMEs?",
    "Tender Status",
    "Tender Status Date",
    "Tender Qualify Reason",
    "Comments",
    "Processed Date",
    "Last Modified Date",
    "Created Date",
]

# Logging
LOG_FILE   = os.path.join(BASE_DIR, "tender_scraper.log")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

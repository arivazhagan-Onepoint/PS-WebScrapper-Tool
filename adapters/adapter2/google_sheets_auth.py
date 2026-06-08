import os
import pickle
import logging
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.exceptions import RefreshError
from .config import SCOPES, CREDENTIALS_FILE, TOKEN_PATH

logger = logging.getLogger(__name__)

def authenticate():
    """Authenticate with Google Sheets API using OAuth 2.0."""
    creds = None

    # Load existing token
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, 'rb') as token_file:
            creds = pickle.load(token_file)
        logger.info(f"Loaded credentials from {TOKEN_PATH}")

    # Refresh or create new credentials
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            logger.info("Token refreshed successfully")
        except RefreshError as e:
            logger.error(f"Failed to refresh token: {e}")
            creds = None

    if not creds or not creds.valid:
        if not os.path.exists(CREDENTIALS_FILE):
            raise FileNotFoundError(
                f"Credentials file not found at {CREDENTIALS_FILE}.\n"
                "Please download OAuth 2.0 credentials from Google Cloud Console."
            )

        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        logger.info("New credentials obtained via OAuth flow")

        # Save token for future use
        with open(TOKEN_PATH, 'wb') as token_file:
            pickle.dump(creds, token_file)
        logger.info(f"Token saved to {TOKEN_PATH}")

    return creds

def get_authenticated_service(service_name='sheets', version='v4'):
    """Get authenticated Google API service."""
    from googleapiclient.discovery import build

    creds = authenticate()
    service = build(service_name, version, credentials=creds)
    logger.info(f"Authenticated service: {service_name} v{version}")
    return service

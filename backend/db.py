"""Supabase client helper. Reads credentials from environment variables only —
never hardcode keys here."""
import os

from supabase import Client, create_client


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    # predict_hourly.py / retrain.py need INSERT/UPDATE rights, so they must run
    # with the service_role key (kept in GitHub Secrets, never in the frontend).
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)

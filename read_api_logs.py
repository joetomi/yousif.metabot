import os
from app import create_app
from models import db, ApiLog

app = create_app()
with app.app_context():
    logs = ApiLog.query.order_by(ApiLog.id.desc()).limit(5).all()
    print("--- LATEST API LOGS ---")
    for log in logs:
        print(f"ID: {log.id} | Timestamp: {log.timestamp}")
        print(f"Endpoint: {log.method} {log.endpoint} | Status: {log.status_code}")
        print(f"Request Payload: {log.request_payload}")
        print(f"Response Payload: {log.response_payload}")
        print(f"Error Code: {log.error_code} | Error Message: {log.error_message}")
        print("-" * 50)

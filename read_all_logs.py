from app import create_app
from models import db, ActivityLog, ApiLog, WebhookLog

app = create_app()
with app.app_context():
    print("=== LATEST WEBHOOK EVENTS RECEIVED ===")
    webhooks = WebhookLog.query.order_by(WebhookLog.id.desc()).limit(5).all()
    for w in webhooks:
        print(f"ID: {w.id} | Received At: {w.received_at} | Status: {w.status}")
        print(f"Payload Snippet: {w.payload[:200]}...")
        if w.error_message:
            print(f"Error: {w.error_message}")
        print("-" * 40)

    print("\n=== LATEST SYSTEM ACTIVITY LOGS ===")
    activities = ActivityLog.query.order_by(ActivityLog.id.desc()).limit(5).all()
    for act in activities:
        print(f"ID: {act.id} | {act.timestamp} | {act.event_type} | {act.status} | {act.message}")
        print("-" * 40)

    print("\n=== LATEST META GRAPH API CALL LOGS ===")
    apis = ApiLog.query.order_by(ApiLog.id.desc()).limit(5).all()
    for api in apis:
        print(f"ID: {api.id} | {api.timestamp} | {api.method} {api.endpoint} | Status: {api.status_code}")
        print(f"Response: {api.response_payload[:300]}...")
        if api.error_message:
            print(f"Error Message: {api.error_message}")
        print("-" * 40)

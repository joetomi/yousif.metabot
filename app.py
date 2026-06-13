import os
from flask import Flask, session
from flask_socketio import SocketIO
from flask_migrate import Migrate
from config import Config
from models import db, Admin, Setting
from translations import translations

# Instantiate extensions globally
socketio = SocketIO(cors_allowed_origins="*", async_mode='threading')
migrate = Migrate()

def create_app():
    """Application factory pattern."""
    app = Flask(__name__)
    app.config.from_object(Config)
    
    # Trust reverse proxy headers (for Render HTTPS)
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    
    # Initialize Database & Migrations
    db.init_app(app)
    migrate.init_app(app, db)
    
    # Initialize SocketIO
    socketio.init_app(app)
    
    # Register Blueprints
    from routes.auth import auth_bp
    from routes.dashboard import dashboard_bp
    from routes.posts import posts_bp
    from routes.settings import settings_bp
    from routes.webhook import webhook_bp
    from routes.messenger import messenger_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(posts_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(webhook_bp)
    app.register_blueprint(messenger_bp)
    
    # Context Processor for Bilingual/Arabic RTL translations
    @app.context_processor
    def inject_translations():
        lang = session.get('lang', 'en')
        if lang not in translations:
            lang = 'en'
        from datetime import datetime
        return dict(
            lang=lang,
            t=translations[lang],
            datetime=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
            page_name=Setting.get("page_name", ""),
            page_id=Setting.get("page_id", "")
        )

    # Server-side inactivity timeout (1 minute)
    @app.before_request
    def check_session_timeout():
        from datetime import datetime, timedelta
        session.permanent = True
        if session.get('admin_logged_in'):
            last_activity_str = session.get('last_activity')
            now = datetime.utcnow()
            if last_activity_str:
                try:
                    last_activity_time = datetime.strptime(last_activity_str, '%Y-%m-%d %H:%M:%S')
                    # Log out if inactive for more than 1 minute
                    if now - last_activity_time > timedelta(minutes=1):
                        session.clear()
                        from flask import flash
                        flash('Session expired due to inactivity. Please log in again.', 'info')
                except Exception:
                    session.clear()
            session['last_activity'] = now.strftime('%Y-%m-%d %H:%M:%S')

    # Initialize Database tables, seed default Admin, and verify setup
    with app.app_context():
        db.create_all()
        
        # 1. Seed Default Admin
        admin = Admin.query.filter_by(username='admin').first()
        if not admin:
            admin = Admin(username='admin')
            # Seed default password
            admin.set_password('admin')
            db.session.add(admin)
            db.session.commit()
            print("Seeded default administrator: Username: 'admin', Password: 'admin'")

        # 2. Seed Default Settings from environment (config.py defaults)
        Setting.set("app_id", Config.DEFAULT_APP_ID)
        Setting.set("app_secret", Config.DEFAULT_APP_SECRET)
        
        def seed_setting(key, config_val):
            val = Setting.get(key)
            if not val or val.strip() == "" or "placeholder" in val.lower():
                Setting.set(key, config_val)

        seed_setting("page_access_token", Config.DEFAULT_PAGE_ACCESS_TOKEN)
        seed_setting("verify_token", Config.DEFAULT_VERIFY_TOKEN)
        seed_setting("page_id", Config.DEFAULT_PAGE_ID)
        seed_setting("tunnel_url", Config.DEFAULT_TUNNEL_URL)
        seed_setting("gemini_api_key", Config.DEFAULT_GEMINI_API_KEY)
        seed_setting("messenger_bot_enabled", Config.DEFAULT_MESSENGER_BOT_ENABLED)
        seed_setting("messenger_bot_tone", Config.DEFAULT_MESSENGER_BOT_TONE)
        seed_setting("messenger_bot_kb", Config.DEFAULT_MESSENGER_BOT_KB)
        
        if not Setting.get("anti_spam_mode"):
            Setting.set("anti_spam_mode", "every_comment")
            
        # Enable Gemini automatically if API key is provided
        if Config.DEFAULT_GEMINI_API_KEY:
            Setting.set("gemini_enabled", "true")
        else:
            if not Setting.get("gemini_enabled"):
                Setting.set("gemini_enabled", "false")
                
        if not Setting.get("gemini_system_instruction"):
            Setting.set("gemini_system_instruction", Config.DEFAULT_GEMINI_SYSTEM_INSTRUCTION)

        # 3. Start APScheduler Background Scheduler only if not in testing and in main worker process
        if os.environ.get("TESTING") != "True" and (not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true'):
            from services.scheduler import init_scheduler
            init_scheduler(app)
        
    return app

app = create_app()

if __name__ == '__main__':
    # Flask-SocketIO runner, listens on dynamic PORT or fallback to 5050
    port = int(os.environ.get("PORT", 5050))
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True)

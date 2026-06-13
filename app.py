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
    from routes.developer import developer_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(posts_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(webhook_bp)
    app.register_blueprint(messenger_bp)
    app.register_blueprint(developer_bp)
    
    # Context Processor for Bilingual/Arabic RTL translations
    @app.context_processor
    def inject_translations():
        lang = session.get('lang', 'ar')
        if lang not in translations:
            lang = 'ar'
        from datetime import datetime
        admin_id = session.get('admin_id')
        is_developer = False
        if admin_id:
            admin = Admin.query.get(admin_id)
            if admin and admin.role == 'developer':
                is_developer = True
        return dict(
            lang=lang,
            t=translations[lang],
            datetime=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
            page_name=Setting.get("page_name", "", user_id=admin_id),
            page_id=Setting.get("page_id", "", user_id=admin_id),
            is_developer=is_developer
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
        try:
            from upgrade_db import run_upgrade
            run_upgrade(app)
        except Exception as e:
            print(f"Database migration/upgrade failure: {e}", flush=True)
            raise e
            
        db.create_all()
        
        # 1. Seed Default Admin (Developer)
        dev_admin = Admin.query.filter_by(username='joetomi').first()
        if not dev_admin:
            dev_admin = Admin(username='joetomi', role='developer')
            dev_admin.set_password('0078707')
            db.session.add(dev_admin)
            db.session.commit()
            print("Seeded default developer: Username: 'joetomi', Password: '0078707'")
        else:
            if dev_admin.role != 'developer':
                dev_admin.role = 'developer'
                db.session.commit()
                
        # Clean up old default 'admin' account if present
        Admin.query.filter_by(username='admin').delete()
        db.session.commit()

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
        seed_setting("messenger_bot_fallback", Config.DEFAULT_MESSENGER_BOT_FALLBACK)
        
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

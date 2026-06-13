from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

class Admin(db.Model):
    __tablename__ = 'admins'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='user')  # 'developer' or 'user'
    is_active = db.Column(db.Boolean, default=True)
    subscription_expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Setting(db.Model):
    __tablename__ = 'settings'
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), nullable=False)
    value = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('admins.id', ondelete='CASCADE'), nullable=True)
    
    # We remove unique constraint from key column because key is now unique per user_id.
    # We will enforce uniqueness per (key, user_id) in database/queries.
    __table_args__ = (
        db.UniqueConstraint('key', 'user_id', name='uq_setting_key_user'),
    )
    
    @staticmethod
    def get(key, default=None, user_id=None):
        if user_id is None:
            from flask import has_request_context, session
            if has_request_context():
                user_id = session.get('admin_id')
        
        if user_id:
            setting = Setting.query.filter_by(key=key, user_id=user_id).first()
            if setting:
                return setting.value
                
        setting = Setting.query.filter_by(key=key, user_id=None).first()
        return setting.value if setting else default
        
    @staticmethod
    def set(key, value, user_id=None):
        if user_id is None:
            from flask import has_request_context, session
            if has_request_context():
                user_id = session.get('admin_id')
                
        global_keys = ["app_id", "app_secret", "verify_token", "tunnel_url"]
        if key in global_keys:
            user_id = None
            
        setting = Setting.query.filter_by(key=key, user_id=user_id).first()
        if setting:
            setting.value = str(value) if value is not None else None
        else:
            setting = Setting(key=key, value=str(value) if value is not None else None, user_id=user_id)
            db.session.add(setting)
        db.session.commit()


class Post(db.Model):
    __tablename__ = 'posts'
    
    id = db.Column(db.String(100), primary_key=True)  # Facebook Post ID
    message = db.Column(db.Text, nullable=True)
    created_time = db.Column(db.DateTime, nullable=True)
    comment_count = db.Column(db.Integer, default=0)
    is_monitored = db.Column(db.Boolean, default=False)
    user_id = db.Column(db.Integer, db.ForeignKey('admins.id', ondelete='CASCADE'), nullable=True)
    
    # Custom templates per post
    default_reply = db.Column(db.Text, default="Thank you for your comment. We have sent details to your inbox.")
    private_message = db.Column(db.Text, default="Hello {name},\n\nThank you for your interest.\n\nHere are the details:\n\n[Custom Message Content]\n\nBest regards.")
    
    last_refreshed = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    comments = db.relationship('Comment', backref='post', lazy=True, cascade="all, delete-orphan")
    messages = db.relationship('Message', backref='post', lazy=True, cascade="all, delete-orphan")


class Comment(db.Model):
    __tablename__ = 'comments'
    
    id = db.Column(db.String(100), primary_key=True)  # Facebook Comment ID
    post_id = db.Column(db.String(100), db.ForeignKey('posts.id'), nullable=False)
    user_id = db.Column(db.String(100), nullable=False)  # Commenter Facebook User ID
    username = db.Column(db.String(150), nullable=True)  # Commenter name
    message = db.Column(db.Text, nullable=True)
    created_time = db.Column(db.DateTime, nullable=True)
    
    processed = db.Column(db.Boolean, default=False)
    reply_sent = db.Column(db.Boolean, default=False)
    reply_id = db.Column(db.String(100), nullable=True)  # Facebook Reply ID
    reply_error = db.Column(db.Text, nullable=True)
    processed_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    messages = db.relationship('Message', backref='comment', lazy=True, cascade="all, delete-orphan")


class Message(db.Model):
    __tablename__ = 'messages'
    
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.String(100), db.ForeignKey('posts.id'), nullable=False)
    comment_id = db.Column(db.String(100), db.ForeignKey('comments.id'), nullable=False)
    user_id = db.Column(db.String(100), nullable=False)  # Recipient FB User ID
    message_content = db.Column(db.Text, nullable=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50), default='PENDING')  # SUCCESS, FAILED
    error_message = db.Column(db.Text, nullable=True)


class ProcessedUser(db.Model):
    __tablename__ = 'processed_users'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=False)
    post_id = db.Column(db.String(100), nullable=True)  # Nullable if globally tracked
    processed_at = db.Column(db.DateTime, default=datetime.utcnow)
    admin_id = db.Column(db.Integer, db.ForeignKey('admins.id', ondelete='CASCADE'), nullable=True)


class MessengerFAQ(db.Model):
    __tablename__ = 'messenger_faqs'
    
    id = db.Column(db.Integer, primary_key=True)
    keyword = db.Column(db.String(100), nullable=False)  # Comma-separated search words
    response = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    admin_id = db.Column(db.Integer, db.ForeignKey('admins.id', ondelete='CASCADE'), nullable=True)


class ProcessedMessage(db.Model):
    __tablename__ = 'processed_messages'
    
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.String(150), unique=True, nullable=False) # Meta message ID (mid)
    processed_at = db.Column(db.DateTime, default=datetime.utcnow)
    admin_id = db.Column(db.Integer, db.ForeignKey('admins.id', ondelete='CASCADE'), nullable=True)



class ApiLog(db.Model):
    __tablename__ = 'api_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    endpoint = db.Column(db.String(250), nullable=False)
    method = db.Column(db.String(10), nullable=False)
    status_code = db.Column(db.Integer, nullable=True)
    request_payload = db.Column(db.Text, nullable=True)
    response_payload = db.Column(db.Text, nullable=True)
    error_code = db.Column(db.Integer, nullable=True)
    error_message = db.Column(db.Text, nullable=True)


class ActivityLog(db.Model):
    __tablename__ = 'activity_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    event_type = db.Column(db.String(50), nullable=False)  # REPLY, MESSAGE, WEBHOOK, SYSTEM, CONFIG_EXPORT, CONFIG_IMPORT
    user_id = db.Column(db.String(100), nullable=True)
    comment_id = db.Column(db.String(100), nullable=True)
    post_id = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(50), nullable=False)  # SUCCESS, FAILED
    message = db.Column(db.Text, nullable=False)
    admin_id = db.Column(db.Integer, db.ForeignKey('admins.id', ondelete='CASCADE'), nullable=True)


class WebhookLog(db.Model):
    __tablename__ = 'webhook_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    payload = db.Column(db.Text, nullable=True)
    received_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50), default='SUCCESS')
    error_message = db.Column(db.Text, nullable=True)

from flask import Blueprint, render_template, jsonify, request, Response
from datetime import datetime, timedelta
import csv
import io
import requests
from routes.auth import admin_required
from models import db, Post, Comment, Message, ActivityLog, Setting, ApiLog, WebhookLog
from services.facebook_api import FacebookApiService

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route('/')
@dashboard_bp.route('/dashboard')
@admin_required
def index():
    from flask import session
    admin_id = session.get('admin_id')
    
    # 1. Fetch Key Metrics
    total_monitored = Post.query.filter_by(is_monitored=True, user_id=admin_id).count()
    total_comments = Comment.query.join(Post).filter(Post.user_id == admin_id).count()
    total_replies = Comment.query.join(Post).filter(Post.user_id == admin_id, Comment.reply_sent == True).count()
    total_messages = Message.query.join(Post).filter(Post.user_id == admin_id, Message.status == 'SUCCESS').count()
    total_webhooks = WebhookLog.query.count()
    
    # 2. Fetch Recent Activities (limit to 10)
    recent_activities = ActivityLog.query.filter_by(admin_id=admin_id).order_by(ActivityLog.timestamp.desc()).limit(10).all()
    
    # 3. Compile Chart Data (Past 7 Days)
    today = datetime.utcnow().date()
    dates_list = [today - timedelta(days=i) for i in range(6, -1, -1)]
    labels = [d.strftime("%Y-%m-%d") for d in dates_list]
    
    # Initialize counts
    comments_by_day = {label: 0 for label in labels}
    replies_by_day = {label: 0 for label in labels}
    messages_by_day = {label: 0 for label in labels}
    
    # Query Database
    start_date = datetime.combine(dates_list[0], datetime.min.time())
    
    # Comments Count
    comments_query = db.session.query(Comment.created_time).join(Post).filter(Post.user_id == admin_id, Comment.created_time >= start_date).all()
    for (ct,) in comments_query:
        if ct:
            date_str = ct.strftime("%Y-%m-%d")
            if date_str in comments_by_day:
                comments_by_day[date_str] += 1
                
    # Replies Count
    replies_query = db.session.query(Comment.processed_at).join(Post).filter(Post.user_id == admin_id, Comment.processed_at >= start_date, Comment.reply_sent == True).all()
    for (pa,) in replies_query:
        if pa:
            date_str = pa.strftime("%Y-%m-%d")
            if date_str in replies_by_day:
                replies_by_day[date_str] += 1
                
    # Messages Count
    messages_query = db.session.query(Message.sent_at).join(Post).filter(Post.user_id == admin_id, Message.sent_at >= start_date, Message.status == 'SUCCESS').all()
    for (sa,) in messages_query:
        if sa:
            date_str = sa.strftime("%Y-%m-%d")
            if date_str in messages_by_day:
                messages_by_day[date_str] += 1

    chart_data = {
        'labels': labels,
        'comments': [comments_by_day[d] for d in labels],
        'replies': [replies_by_day[d] for d in labels],
        'messages': [messages_by_day[d] for d in labels]
    }
    
    return render_template(
        'dashboard.html',
        total_monitored=total_monitored,
        total_comments=total_comments,
        total_replies=total_replies,
        total_messages=total_messages,
        total_webhooks=total_webhooks,
        recent_activities=recent_activities,
        chart_data=chart_data
    )

@dashboard_bp.route('/api/stats')
@admin_required
def stats_api():
    from flask import session
    admin_id = session.get('admin_id')
    total_monitored = Post.query.filter_by(is_monitored=True, user_id=admin_id).count()
    total_comments = Comment.query.join(Post).filter(Post.user_id == admin_id).count()
    total_replies = Comment.query.join(Post).filter(Post.user_id == admin_id, Comment.reply_sent == True).count()
    total_messages = Message.query.join(Post).filter(Post.user_id == admin_id, Message.status == 'SUCCESS').count()
    total_webhooks = WebhookLog.query.count()
    
    return jsonify({
        'total_monitored': total_monitored,
        'total_comments': total_comments,
        'total_replies': total_replies,
        'total_messages': total_messages,
        'total_webhooks': total_webhooks
    })

@dashboard_bp.route('/logs')
@admin_required
def view_logs():
    # Filters
    log_filter = request.args.get('filter', 'all')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    from flask import session
    admin_id = session.get('admin_id')
    
    query = ActivityLog.query.filter_by(admin_id=admin_id)
    
    if log_filter == 'success':
        query = query.filter_by(status='SUCCESS')
    elif log_filter == 'failed':
        query = query.filter_by(status='FAILED')
    elif log_filter == 'replies':
        query = query.filter_by(event_type='REPLY')
    elif log_filter == 'messages':
        query = query.filter_by(event_type='MESSAGE')
        
    pagination = query.order_by(ActivityLog.timestamp.desc()).paginate(page=page, per_page=per_page, error_out=False)
    logs = pagination.items
    
    return render_template('logs.html', logs=logs, pagination=pagination, current_filter=log_filter)

@dashboard_bp.route('/logs/export')
@admin_required
def export_logs():
    log_filter = request.args.get('filter', 'all')
    from flask import session
    admin_id = session.get('admin_id')
    
    query = ActivityLog.query.filter_by(admin_id=admin_id)
    if log_filter == 'success':
        query = query.filter_by(status='SUCCESS')
    elif log_filter == 'failed':
        query = query.filter_by(status='FAILED')
    elif log_filter == 'replies':
        query = query.filter_by(event_type='REPLY')
    elif log_filter == 'messages':
        query = query.filter_by(event_type='MESSAGE')
        
    logs = query.order_by(ActivityLog.timestamp.desc()).all()
    
    # Generate CSV in memory
    si = io.StringIO()
    cw = csv.writer(si)
    
    # Write header
    cw.writerow(['ID', 'Timestamp', 'Event Type', 'User ID', 'Comment ID', 'Post ID', 'Status', 'Message'])
    
    # Write data
    for log in logs:
        cw.writerow([
            log.id,
            log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            log.event_type,
            log.user_id or '',
            log.comment_id or '',
            log.post_id or '',
            log.status,
            log.message
        ])
        
    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename=activity_logs_{log_filter}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.csv"}
    )

@dashboard_bp.route('/status')
@admin_required
def status():
    return render_template('status.html')

@dashboard_bp.route('/api/status')
@admin_required
def api_status():
    """Compiles health metrics for database, Meta API connection, webhook, and tunnel."""
    from flask import session
    admin_id = session.get('admin_id')
    
    # 1. Database Status
    db_ok = False
    try:
        # Simple query to check DB availability
        Setting.query.first()
        db_ok = True
    except Exception:
        pass
        
    # 2. Meta Facebook API Status
    fb_ok = False
    fb_details = "Token / Page ID missing"
    token = Setting.get("page_access_token", user_id=admin_id)
    page_id = Setting.get("page_id", user_id=admin_id)
    
    if token and page_id:
        api = FacebookApiService(page_access_token=token, page_id=page_id)
        success, msg = api.test_connection()
        if success:
            fb_ok = True
            fb_details = "Connected to Facebook API"
        else:
            fb_details = msg
            
    # 3. Tunnel & Webhook URL Status
    tunnel_ok = False
    tunnel_details = "Not Configured"
    tunnel_url = Setting.get("tunnel_url")
    
    if tunnel_url:
        tunnel_details = f"Configured: {tunnel_url}"
        try:
            # Send a quick request to local tunnel URL to verify if online
            # Use short timeout so we don't hang
            resp = requests.head(tunnel_url, timeout=3)
            if resp.status_code < 500:
                tunnel_ok = True
                tunnel_details = f"Active ({resp.status_code}): {tunnel_url}"
            else:
                tunnel_details = f"Returned status {resp.status_code}: {tunnel_url}"
        except Exception as e:
            tunnel_details = f"Unreachable: {str(e)}"

    # 4. Webhook Subscription Status
    webhook_ok = False
    webhook_details = "Pending Subscription"
    verify_token = Setting.get("verify_token")
    
    if verify_token:
        webhook_ok = True
        webhook_details = f"Token set. Webhook: {tunnel_url.rstrip('/')}/webhook" if tunnel_url else "Token set, but tunnel url missing"
        
    return jsonify({
        'database': {'status': 'OK' if db_ok else 'ERROR', 'details': 'SQLite Database online' if db_ok else 'Database connection error'},
        'facebook_api': {'status': 'OK' if fb_ok else 'ERROR', 'details': fb_details},
        'tunnel': {'status': 'OK' if tunnel_ok else 'ERROR', 'details': tunnel_details},
        'webhook': {'status': 'OK' if webhook_ok else 'ERROR', 'details': webhook_details}
    })

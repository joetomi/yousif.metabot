from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from datetime import datetime
from routes.auth import admin_required
from models import db, Post
from services.facebook_api import FacebookApiService
from services.comment_processor import log_activity, trigger_dashboard_update

posts_bp = Blueprint('posts', __name__)

@posts_bp.route('/posts')
@admin_required
def index():
    from flask import session
    admin_id = session.get('admin_id')
    posts = Post.query.filter((Post.user_id == admin_id) | (Post.user_id == None)).order_by(Post.created_time.desc()).all()
    return render_template('posts.html', posts=posts)

@posts_bp.route('/posts/refresh', methods=['POST'])
@admin_required
def refresh_posts():
    """Manual trigger to pull recent posts from Meta Graph API."""
    from models import Setting
    from flask import session
    admin_id = session.get('admin_id')
    
    page_access_token = Setting.get("page_access_token", user_id=admin_id)
    page_id = Setting.get("page_id", user_id=admin_id)
    
    if not page_access_token or not page_id:
        return jsonify({"status": "error", "message": "Please configure Page Access Token and Page ID first."}), 400
        
    api = FacebookApiService(page_access_token=page_access_token, page_id=page_id)
    posts_data = api.fetch_posts(limit=25)
    
    if not posts_data:
        return jsonify({"status": "error", "message": "Failed to fetch posts. Check credentials and page access."}), 400
        
    try:
        new_count = 0
        updated_count = 0
        
        for p_data in posts_data:
            p_id = p_data["id"]
            post = Post.query.filter((Post.id == p_id) & ((Post.user_id == admin_id) | (Post.user_id == None))).first()
            
            # Formulate created time
            c_time = None
            if p_data.get("created_time"):
                try:
                    c_time = datetime.strptime(p_data["created_time"].split("+")[0], "%Y-%m-%dT%H:%M:%S")
                except Exception:
                    pass
            
            if post:
                post.comment_count = p_data["comment_count"]
                post.message = p_data["message"]
                updated_count += 1
            else:
                post = Post(
                    id=p_id,
                    message=p_data["message"],
                    created_time=c_time,
                    comment_count=p_data["comment_count"],
                    is_monitored=False,
                    user_id=admin_id
                )
                db.session.add(post)
                new_count += 1
                
        db.session.commit()
        
        log_activity(
            event_type="SYSTEM",
            status="SUCCESS",
            message=f"Manual Posts Refresh: {new_count} new, {updated_count} updated posts."
        )
        trigger_dashboard_update()
        
        return jsonify({
            "status": "success", 
            "message": f"Refresh completed successfully! Synced {new_count + updated_count} posts."
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": f"Database error: {str(e)}"}), 500

@posts_bp.route('/posts/toggle-monitoring/<post_id>', methods=['POST'])
@admin_required
def toggle_monitoring(post_id):
    """Enables or disables comment/message monitoring on a specific post."""
    from flask import session
    admin_id = session.get('admin_id')
    post = Post.query.filter((Post.id == post_id) & ((Post.user_id == admin_id) | (Post.user_id == None))).first()
    if not post:
        return jsonify({"status": "error", "message": "Post not found."}), 404
        
    try:
        data = request.get_json() or {}
        is_monitored = data.get("is_monitored", not post.is_monitored)
        
        post.is_monitored = is_monitored
        db.session.commit()
        
        status_str = "enabled" if is_monitored else "disabled"
        log_activity(
            event_type="SYSTEM",
            status="SUCCESS",
            message=f"Post monitoring {status_str} for post ID: {post_id}.",
            post_id=post_id
        )
        trigger_dashboard_update(admin_id=admin_id)
        
        return jsonify({
            "status": "success", 
            "message": f"Monitoring {status_str} successfully for this post.",
            "is_monitored": post.is_monitored
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
 
@posts_bp.route('/posts/update-templates/<post_id>', methods=['POST'])
@admin_required
def update_templates(post_id):
    """Updates the custom comment reply and private message templates for a post."""
    from flask import session
    admin_id = session.get('admin_id')
    post = Post.query.filter((Post.id == post_id) & ((Post.user_id == admin_id) | (Post.user_id == None))).first()
    if not post:
        flash("Post not found.", "danger")
        return redirect(url_for('posts.index'))
        
    try:
        default_reply = request.form.get("default_reply", "").strip()
        private_message = request.form.get("private_message", "").strip()
        
        if not default_reply or not private_message:
            flash("Templates cannot be empty.", "warning")
            return redirect(url_for('posts.index'))
            
        post.default_reply = default_reply
        post.private_message = private_message
        db.session.commit()
        
        log_activity(
            event_type="SYSTEM",
            status="SUCCESS",
            message=f"Custom reply templates updated for post ID: {post_id}.",
            post_id=post_id
        )
        flash("Templates updated successfully!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error updating templates: {str(e)}", "danger")
        
    return redirect(url_for('posts.index'))

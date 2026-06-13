from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from models import db, Post, Setting
from services.facebook_api import FacebookApiService
from services.comment_processor import log_activity, trigger_dashboard_update

scheduler = BackgroundScheduler()

def sync_posts_job(app):
    """Background task to synchronize page posts and update comment counts."""
    with app.app_context():
        # Check if settings are configured
        token = Setting.get("page_access_token")
        page_id = Setting.get("page_id")
        if not token or not page_id:
            return

        print("Executing periodic posts synchronization...")
        api = FacebookApiService()
        posts_data = api.fetch_posts(limit=25)
        
        if not posts_data:
            print("No posts fetched or credentials invalid.")
            return
            
        updated_count = 0
        new_count = 0
        
        try:
            for p_data in posts_data:
                p_id = p_data["id"]
                post = Post.query.get(p_id)
                
                if post:
                    post.comment_count = p_data["comment_count"]
                    post.message = p_data["message"]
                    updated_count += 1
                else:
                    post = Post(
                        id=p_id,
                        message=p_data["message"],
                        comment_count=p_data["comment_count"],
                        is_monitored=False  # default to False
                    )
                    db.session.add(post)
                    new_count += 1
                    
            db.session.commit()
            print(f"Posts Sync completed: {new_count} new, {updated_count} updated.")
            
            # Record success in log
            log_activity(
                event_type="SYSTEM",
                status="SUCCESS",
                message=f"Sync Posts: {new_count} new, {updated_count} updated posts synced."
            )
            trigger_dashboard_update()
        except Exception as e:
            db.session.rollback()
            print(f"Error during posts synchronization: {e}")
            log_activity(
                event_type="SYSTEM",
                status="FAILED",
                message=f"Sync Posts failed: {str(e)}"
            )

def poll_comments_job(app):
    """Background task to poll monitored posts for new comments and process them."""
    import requests
    with app.app_context():
        token = Setting.get("page_access_token")
        page_id = Setting.get("page_id")
        if not token or not page_id:
            return

        print("Executing periodic comments polling...")
        
        # Get all monitored posts
        monitored_posts = Post.query.filter_by(is_monitored=True).all()
        if not monitored_posts:
            return

        api = FacebookApiService()
        
        for post in monitored_posts:
            # Fetch comments for this post
            endpoint = f"https://graph.facebook.com/v23.0/{post.id}/comments"
            params = {
                "access_token": token,
                "fields": "id,message,from,created_time",
                "limit": 50
            }
            
            try:
                response = requests.get(endpoint, params=params, timeout=15)
                # Log API call
                api._log_call(f"/{post.id}/comments", "GET", params, response)
                
                if response.status_code == 200:
                    comments_data = response.json().get("data", [])
                    for comment in comments_data:
                        comment_id = comment.get("id")
                        
                        # Check if comment is already processed or exists
                        from models import Comment
                        existing = Comment.query.get(comment_id)
                        if existing:
                            continue
                            
                        # If not existing or not processed, process it
                        sender_name = comment.get("from", {}).get("name", "Facebook User")
                        sender_id = comment.get("from", {}).get("id", f"anon_{comment_id}")
                        message_text = comment.get("message", "")
                        created_time_str = comment.get("created_time")
                        
                        comment_data = {
                            "comment_id": comment_id,
                            "post_id": post.id,
                            "user_id": sender_id,
                            "username": sender_name,
                            "message": message_text,
                            "created_time": created_time_str
                        }
                        
                        print(f"Polling found new comment {comment_id} from {sender_name}. Processing...")
                        
                        # Process the comment directly
                        from services.comment_processor import process_comment_job
                        process_comment_job(app, comment_data)
                        
            except Exception as e:
                print(f"Error polling comments for post {post.id}: {e}")

def init_scheduler(app):
    """Initializes and starts the background scheduler."""
    if not scheduler.running:
        # Schedule the posts sync job to run every 15 minutes
        scheduler.add_job(
            func=sync_posts_job,
            trigger=IntervalTrigger(minutes=15),
            args=[app],
            id="sync_posts_job",
            name="Synchronize recent Facebook Page posts",
            replace_existing=True
        )
        # Schedule the comments polling job to run every 10 seconds as a fast fallback
        scheduler.add_job(
            func=poll_comments_job,
            trigger=IntervalTrigger(seconds=10),
            args=[app],
            id="poll_comments_job",
            name="Poll monitored posts for comments",
            replace_existing=True
        )
        scheduler.start()
        print("APScheduler background scheduler started.")

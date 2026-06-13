import requests
import json
from datetime import datetime
from models import db, Setting, ApiLog, ActivityLog

GRAPH_API_VERSION = "v23.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

class FacebookApiService:
    def __init__(self, page_access_token=None, page_id=None):
        self.token = page_access_token or Setting.get("page_access_token")
        self.page_id = page_id or Setting.get("page_id")

    def _log_call(self, endpoint, method, payload, response):
        """Helper to write API logs into the SQLite database."""
        try:
            status_code = response.status_code
            response_text = response.text
            
            # Try parsing JSON error code and error message
            error_code = None
            error_message = None
            try:
                resp_json = response.json()
                if "error" in resp_json:
                    error_code = resp_json["error"].get("code")
                    error_message = resp_json["error"].get("message")
            except Exception:
                pass

            log_entry = ApiLog(
                endpoint=endpoint,
                method=method,
                status_code=status_code,
                request_payload=json.dumps(payload) if payload else None,
                response_payload=response_text,
                error_code=error_code,
                error_message=error_message
            )
            db.session.add(log_entry)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Error logging API call: {e}")

    def test_connection(self):
        """Tests the Page Access Token by retrieving page details."""
        if not self.token or not self.page_id:
            return False, "Missing credentials in system settings."
        
        endpoint = f"{BASE_URL}/{self.page_id}"
        params = {"access_token": self.token, "fields": "id,name,category,username"}
        
        try:
            response = requests.get(endpoint, params=params, timeout=10)
            self._log_call(f"/{self.page_id}", "GET", params, response)
            
            if response.status_code == 200:
                data = response.json()
                return True, f"Connected successfully to Page: {data.get('name')}"
            else:
                err_msg = response.json().get("error", {}).get("message", "Unknown Meta Graph API error.")
                return False, f"API Error (Status {response.status_code}): {err_msg}"
        except Exception as e:
            return False, f"Network connection error: {str(e)}"

    def subscribe_page(self):
        """Subscribes the Page to the App so Facebook sends webhook notifications."""
        if not self.token or not self.page_id:
            return False, "Missing credentials in system settings."
        
        endpoint = f"{BASE_URL}/{self.page_id}/subscribed_apps"
        params = {
            "access_token": self.token,
            "subscribed_fields": "feed"
        }
        
        try:
            response = requests.post(endpoint, params=params, timeout=10)
            self._log_call(f"/{self.page_id}/subscribed_apps", "POST", params, response)
            
            if response.status_code == 200 and response.json().get("success") is True:
                return True, "Successfully subscribed Page to App webhooks."
            else:
                err_data = response.json().get("error", {})
                err_msg = err_data.get("message", "Unknown Meta Graph API error.")
                return False, f"API Error (Status {response.status_code}): {err_msg}"
        except Exception as e:
            return False, f"Connection error during subscription: {str(e)}"

    def get_page_info(self):
        """Retrieves page info like Name and Username."""
        if not self.token or not self.page_id:
            return None
        
        endpoint = f"{BASE_URL}/{self.page_id}"
        params = {"access_token": self.token, "fields": "id,name,category,username,link"}
        
        try:
            response = requests.get(endpoint, params=params, timeout=10)
            self._log_call(f"/{self.page_id}", "GET", params, response)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            print(f"Error getting page info: {e}")
        return None

    def fetch_posts(self, limit=25):
        """Fetches recent posts from the page."""
        if not self.token or not self.page_id:
            return []
        
        endpoint = f"{BASE_URL}/{self.page_id}/posts"
        params = {
            "access_token": self.token,
            "fields": "id,message,created_time,shares,comments.summary(true).limit(0)",
            "limit": limit
        }
        
        try:
            response = requests.get(endpoint, params=params, timeout=10)
            self._log_call(f"/{self.page_id}/posts", "GET", params, response)
            
            if response.status_code == 200:
                data = response.json()
                posts_list = []
                for p in data.get("data", []):
                    # Extract comment count
                    comments_summary = p.get("comments", {}).get("summary", {})
                    comment_count = comments_summary.get("total_count", 0)
                    
                    posts_list.append({
                        "id": p.get("id"),
                        "message": p.get("message", "[No Message Content]"),
                        "created_time": p.get("created_time"),
                        "comment_count": comment_count
                    })
                return posts_list
            else:
                print(f"Graph API returned status {response.status_code} on fetching posts")
                return []
        except Exception as e:
            print(f"Error fetching posts: {e}")
            return []

    def reply_to_comment(self, comment_id, message):
        """Replies to a comment publicly."""
        if not self.token:
            return False, "Missing Page Access Token", None
        
        endpoint = f"{BASE_URL}/{comment_id}/comments"
        payload = {"message": message}
        params = {"access_token": self.token}
        
        try:
            response = requests.post(endpoint, params=params, json=payload, timeout=10)
            self._log_call(f"/{comment_id}/comments", "POST", payload, response)
            
            if response.status_code == 200:
                data = response.json()
                return True, "Reply sent successfully", data.get("id")
            else:
                err_data = response.json().get("error", {})
                err_msg = err_data.get("message", "Unknown Meta Graph API error.")
                return False, f"API Error (Status {response.status_code}): {err_msg}", None
        except Exception as e:
            return False, f"Exception occurred: {str(e)}", None

    def send_private_reply(self, comment_id, message):
        """Sends a private message (private reply) to a commenter."""
        if not self.token or not self.page_id:
            return False, "Missing credentials"
        
        # Modern Messenger Platform Send API: POST /{page-id}/messages
        endpoint = f"{BASE_URL}/{self.page_id}/messages"
        payload = {
            "recipient": {
                "comment_id": comment_id
            },
            "message": {
                "text": message
            }
        }
        params = {"access_token": self.token}
        
        try:
            response = requests.post(endpoint, params=params, json=payload, timeout=10)
            self._log_call(f"/{self.page_id}/messages", "POST", payload, response)
            
            if response.status_code == 200:
                return True, "Private reply sent successfully"
            else:
                err_data = response.json().get("error", {})
                err_msg = err_data.get("message", "Unknown Meta Graph API error.")
                err_code = err_data.get("code")
                return False, f"API Error (Code {err_code}): {err_msg}"
        except Exception as e:
            return False, f"Exception occurred: {str(e)}"

    def send_messenger_message(self, recipient_id, text):
        """Sends a direct message to a user on Messenger."""
        if not self.token or not self.page_id:
            return False, "Missing credentials"
        
        endpoint = f"{BASE_URL}/{self.page_id}/messages"
        payload = {
            "recipient": {
                "id": recipient_id
            },
            "message": {
                "text": text
            }
        }
        params = {"access_token": self.token}
        
        try:
            response = requests.post(endpoint, params=params, json=payload, timeout=10)
            self._log_call(f"/{self.page_id}/messages", "POST", payload, response)
            
            if response.status_code == 200:
                return True, "Messenger message sent successfully"
            else:
                err_data = response.json().get("error", {})
                err_msg = err_data.get("message", "Unknown Meta Graph API error.")
                err_code = err_data.get("code")
                return False, f"API Error (Code {err_code}): {err_msg}"
        except Exception as e:
            return False, f"Exception occurred: {str(e)}"

    @staticmethod
    def parse_template(template_str, user_name, comment_text, post_id, comment_date):
        """Replaces variables in a message template."""
        if not template_str:
            return ""
        
        # Clean up fallback values if inputs are empty
        user_name = user_name or "there"
        comment_text = comment_text or ""
        post_id = post_id or ""
        
        if isinstance(comment_date, datetime):
            date_str = comment_date.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(comment_date, str):
            date_str = comment_date
        else:
            date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
        replacements = {
            "{name}": user_name,
            "{comment}": comment_text,
            "{post_id}": post_id,
            "{date}": date_str
        }
        
        parsed = template_str
        for variable, val in replacements.items():
            parsed = parsed.replace(variable, str(val))
            
        return parsed

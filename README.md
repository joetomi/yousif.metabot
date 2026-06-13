# Facebook Comment Auto Responder & Messenger Bot

A production-ready Facebook Page Automation System built with **Python Flask**, **SQLite (SQLAlchemy)**, **Bootstrap 5**, and **Flask-SocketIO**. 

This system monitors comments on selected Facebook Page posts via Meta Webhooks, automatically replies once to comments publicly, sends private replies to commenters via Messenger, protects against duplicate replies/messages (anti-spam), and logs all operations in real-time.

---

## Features

- **Secure Authentication**: Admin login with password hashing.
- **Dynamic Dashboard**: Real-time metric card updates and visual charts (daily comments, replies, messages) powered by Flask-SocketIO.
- **Anti-Spam Frequency Controls**:
  - Reply to every comment.
  - Reply once per user per post.
  - Reply once per user globally.
- **Bilingual Support**: Fully responsive English & Arabic (RTL) localization.
- **Webhook Security**: Verification of all incoming POST webhooks using HMAC-SHA256 signature checking (`X-Hub-Signature-256`) and the Facebook App Secret.
- **Message Templates with Variables**: Supports variables `{name}`, `{comment}`, `{post_id}`, and `{date}`.
- **Detailed Audit & API Logging**: Detailed logs of every Meta Graph API call, including HTTP status, payload, response, and error codes.
- **Backup Support**: Export and import settings, posts list, and templates in JSON format.
- **Docker Support**: Containerized environment ready for deployment.

---

## Project Structure

```
facebook_bot/
│
├── app.py                  # Main Flask app initializer
├── config.py               # Flask & environment settings loader
├── models.py               # Database models & SQLAlchemy ORM
├── translations.py         # English and Arabic translation dictionaries
├── requirements.txt        # Pinned dependencies
├── Dockerfile              # Docker container setup
├── docker-compose.yml      # Multi-container orchestrator config
├── .env.example            # Environment variables template
├── .env                    # Active configuration parameters
│
├── routes/
│   ├── auth.py             # Login, logout, language toggle
│   ├── dashboard.py        # Stats, logs, CSV export, status checker
│   ├── settings.py         # Page token, connection tests, config import/export
│   ├── posts.py            # Posts sync, monitoring toggle, edit templates
│   └── webhook.py          # Meta webhook challenge validation & payload signature check
│
├── services/
│   ├── facebook_api.py     # Graph API connection, public/private reply dispatchers
│   ├── comment_processor.py # Comment queue processor & anti-spam validation
│   └── scheduler.py        # Periodic sync tasks using APScheduler
│
├── static/
│   ├── css/
│   │   └── style.css       # Custom Glassmorphism dark-theme styling
│   └── js/
│       └── main.js         # SocketIO stats updates, poller, and charts controller
│
└── templates/              # HTML views (extends base.html)
    ├── base.html
    ├── login.html
    ├── dashboard.html
    ├── settings.html
    ├── posts.html
    ├── logs.html
    └── status.html
```

---

## Installation & Local Execution

### Prerequisites
- Python 3.12+ installed.
- Git.

### Step 1: Clone and Enter Directory
Navigate to your project directory. Keep this folder as your active workspace:
```bash
cd facebook_bot
```

### Step 2: Set Up Virtual Environment & Install Dependencies
Create a virtual environment and install the pinned requirements:
```bash
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

### Step 3: Configure Environment Variables
Copy the template environment file:
```bash
cp .env.example .env
```
Open `.env` and fill in your details:
- `SECRET_KEY`: Set to any long random string.
- `APP_SECRET`: Your Meta App Secret (from the Meta App dashboard).
- `PAGE_ACCESS_TOKEN`: Facebook Page Access Token with `pages_read_engagement`, `pages_manage_metadata`, and `pages_messaging` permissions.
- `VERIFY_TOKEN`: A custom string token you choose (e.g. `my_secret_token_123`) to verify your webhook in Meta.
- `PAGE_ID`: The Page ID of your Facebook page.
- `TUNNEL_URL`: The URL of your tunnel (e.g., `https://ready-otters-happen.loca.lt`).

### Step 4: Run the Application
Start the Flask application:
```bash
python app.py
```
The server will start on `0.0.0.0:5050`.

Open your browser and navigate to `http://localhost:5050`.

**Default Login Credentials**:
- **Username**: `admin`
- **Password**: `admin`

---

## Deployment with Docker

You can run the entire system in a Docker container with one command.

### Step 1: Build and Run Container
```bash
docker compose up -d
```
Docker will pull Python 3.12, install dependencies, create a local `./data` folder on your host to persist the SQLite database, and run the service.

### Step 2: Verify Log Status
```bash
docker compose logs -f
```

---

## Webhook Setup & Tunnel Configuration

Meta requires an `https` URL to send webhook events to your local server.

### 1. Set Up a Local Tunnel
You can expose port `5050` using `localtunnel` or `ngrok`:

Using **localtunnel**:
```bash
npx localtunnel --port 5050 --subdomain ready-otters-happen
```

Save the generated URL (e.g. `https://ready-otters-happen.loca.lt`) to the **Tunnel URL** field in the Settings page of the dashboard or in your `.env` file.

### 2. Configure Meta Developer Console
1. Go to [Meta for Developers](https://developers.facebook.com/).
2. Select your App, click **Add Product** and choose **Webhooks**.
3. Under Webhooks dropdown, select **Page**.
4. Click **Subscribe to this object**:
   - **Callback URL**: `https://ready-otters-happen.loca.lt/webhook` (or your configured tunnel URL + `/webhook`).
   - **Verify Token**: Must match the `VERIFY_TOKEN` you set in Settings / `.env`.
5. Click **Verify and Save**.
6. In the Page Subscription Webhook Fields table, find **feed** and click **Subscribe**.

---

## Backup & Restore Configurations
You can download your entire configuration (settings, posts toggles, and customized reply templates) as a JSON file by clicking **Export Backup** on the Settings page.

To restore your setup on a new system or container, simply select the backup JSON file and click **Restore Configuration** on the Settings page.
"# yousif.metabot" 

document.addEventListener("DOMContentLoaded", function () {
    // Mobile Sidebar Toggle
    const sidebar = document.querySelector(".sidebar");
    const toggleBtn = document.querySelector(".sidebar-toggle");
    
    if (toggleBtn && sidebar) {
        toggleBtn.addEventListener("click", function () {
            sidebar.classList.toggle("open");
        });
        
        // Close sidebar if user clicks outside on mobile
        document.addEventListener("click", function (event) {
            if (!sidebar.contains(event.target) && !toggleBtn.contains(event.target)) {
                sidebar.classList.remove("open");
            }
        });
    }

    // Connect to Flask-SocketIO
    if (typeof io !== 'undefined') {
        const socket = io();
        
        socket.on('connect', function() {
            console.log('Connected to server via SocketIO');
        });

        // Listen for stats_update events
        socket.on('stats_update', function(data) {
            console.log('Received real-time stats update:', data);
            
            // Update counts in dashboard with animation if elements exist
            updateCounter('total-monitored-val', data.total_monitored);
            updateCounter('total-comments-val', data.total_comments);
            updateCounter('total-replies-val', data.total_replies);
            updateCounter('total-messages-val', data.total_messages);
            updateCounter('total-webhooks-val', data.total_webhooks);
        });
    }

    // Helper for counting up animation
    function updateCounter(id, newValue) {
        const el = document.getElementById(id);
        if (el) {
            const oldValue = parseInt(el.innerText) || 0;
            if (oldValue !== newValue) {
                // Instantly update value or animate
                el.innerText = newValue;
                el.classList.add('text-success');
                setTimeout(() => {
                    el.classList.remove('text-success');
                }, 1000);
            }
        }
    }

    // Connection testing
    const testConnBtn = document.getElementById("test-connection-btn");
    if (testConnBtn) {
        testConnBtn.addEventListener("click", function () {
            const origText = testConnBtn.innerHTML;
            testConnBtn.disabled = true;
            testConnBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Testing...';
            
            const form = document.getElementById("settings-form");
            const formData = new FormData(form);
            
            fetch("/settings/test-connection", {
                method: "POST",
                body: formData
            })
            .then(response => response.json().then(data => ({ status: response.status, body: data })))
            .then(res => {
                testConnBtn.disabled = false;
                testConnBtn.innerHTML = origText;
                
                if (res.status === 200) {
                    showToast("success", res.body.message);
                } else {
                    showToast("danger", res.body.message);
                }
            })
            .catch(err => {
                testConnBtn.disabled = false;
                testConnBtn.innerHTML = origText;
                showToast("danger", "Network error occurred. Try again.");
            });
        });
    }

    // Page Refresh Info
    const refreshPageBtn = document.getElementById("refresh-page-btn");
    if (refreshPageBtn) {
        refreshPageBtn.addEventListener("click", function () {
            const origText = refreshPageBtn.innerHTML;
            refreshPageBtn.disabled = true;
            refreshPageBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Loading...';
            
            fetch("/settings/refresh-page", {
                method: "POST"
            })
            .then(response => response.json().then(data => ({ status: response.status, body: data })))
            .then(res => {
                refreshPageBtn.disabled = false;
                refreshPageBtn.innerHTML = origText;
                
                if (res.status === 200) {
                    showToast("success", res.body.message);
                    setTimeout(() => window.location.reload(), 1500);
                } else {
                    showToast("danger", res.body.message);
                }
            })
            .catch(err => {
                refreshPageBtn.disabled = false;
                refreshPageBtn.innerHTML = origText;
                showToast("danger", "Failed to contact local server.");
            });
        });
    }

    // Refresh Posts Manager
    const refreshPostsBtn = document.getElementById("refresh-posts-btn");
    if (refreshPostsBtn) {
        refreshPostsBtn.addEventListener("click", function () {
            const origText = refreshPostsBtn.innerHTML;
            refreshPostsBtn.disabled = true;
            refreshPostsBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Fetching...';
            
            fetch("/posts/refresh", {
                method: "POST"
            })
            .then(response => response.json().then(data => ({ status: response.status, body: data })))
            .then(res => {
                refreshPostsBtn.disabled = false;
                refreshPostsBtn.innerHTML = origText;
                
                if (res.status === 200) {
                    showToast("success", res.body.message);
                    setTimeout(() => window.location.reload(), 1500);
                } else {
                    showToast("danger", res.body.message);
                }
            })
            .catch(err => {
                refreshPostsBtn.disabled = false;
                refreshPostsBtn.innerHTML = origText;
                showToast("danger", "Failed to sync posts.");
            });
        });
    }

    // Toggle Monitoring Switch
    const monitorSwitches = document.querySelectorAll(".monitor-switch");
    monitorSwitches.forEach(sw => {
        sw.addEventListener("change", function () {
            const postId = sw.dataset.postId;
            const checked = sw.checked;
            
            fetch(`/posts/toggle-monitoring/${postId}`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ is_monitored: checked })
            })
            .then(response => response.json())
            .then(data => {
                if (data.status === "success") {
                    showToast("success", data.message);
                } else {
                    sw.checked = !checked; // revert
                    showToast("danger", data.message);
                }
            })
            .catch(err => {
                sw.checked = !checked; // revert
                showToast("danger", "Failed to update monitoring state.");
            });
        });
    });

    // API Status Page live refresh (every 30 seconds)
    const statusContainer = document.getElementById("status-indicators");
    if (statusContainer) {
        // Poll status immediately, then every 30s
        updateStatusPage();
        setInterval(updateStatusPage, 30000);
    }

    function updateStatusPage() {
        fetch("/api/status")
        .then(response => response.json())
        .then(data => {
            updateIndicator("db-status", "db-details", data.database);
            updateIndicator("api-status", "api-details", data.facebook_api);
            updateIndicator("tunnel-status", "tunnel-details", data.tunnel);
            updateIndicator("webhook-status", "webhook-details", data.webhook);
            
            // Update last updated timestamp
            const timeEl = document.getElementById("status-last-updated");
            if (timeEl) {
                timeEl.innerText = new Date().toLocaleTimeString();
            }
        })
        .catch(err => {
            console.error("Error fetching status details:", err);
        });
    }

    function updateIndicator(cardId, detailsId, item) {
        const card = document.getElementById(cardId);
        const details = document.getElementById(detailsId);
        
        if (card && details) {
            details.innerText = item.details;
            
            const badge = card.querySelector(".badge");
            if (badge) {
                if (item.status === "OK") {
                    badge.className = "badge badge-emerald";
                    badge.innerText = "ONLINE";
                    card.style.borderColor = "rgba(17, 202, 160, 0.2)";
                } else {
                    badge.className = "badge badge-red";
                    badge.innerText = "ERROR";
                    card.style.borderColor = "rgba(239, 68, 68, 0.2)";
                }
            }
        }
    }

    // Dynamic Toast Alert
    function showToast(type, message) {
        const toastContainer = document.getElementById("toast-container");
        if (!toastContainer) {
            // Create container if missing
            const container = document.createElement("div");
            container.id = "toast-container";
            container.style.position = "fixed";
            container.style.top = "20px";
            container.style.right = "20px";
            container.style.zIndex = "9999";
            document.body.appendChild(container);
        }
        
        const isRtl = document.documentElement.dir === 'rtl';
        const tc = document.getElementById("toast-container");
        if (isRtl) {
            tc.style.right = 'auto';
            tc.style.left = '20px';
        }
        
        const toast = document.createElement("div");
        toast.className = `toast align-items-center text-white bg-${type === 'success' ? 'success' : 'danger'} border-0 show m-2`;
        toast.role = "alert";
        toast.ariaLive = "assertive";
        toast.ariaAtomic = "true";
        toast.style.background = type === 'success' ? 'rgba(17, 202, 160, 0.85) !important' : 'rgba(239, 68, 68, 0.85) !important';
        toast.style.backdropFilter = "blur(8px)";
        toast.style.border = "1px solid rgba(255, 255, 255, 0.1)";
        toast.style.borderRadius = "8px";
        toast.style.boxShadow = "0 4px 12px rgba(0,0,0,0.15)";
        
        toast.innerHTML = `
            <div class="d-flex">
                <div class="toast-body fw-bold">
                    ${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
        `;
        
        tc.appendChild(toast);
        
        // Remove toast after 4 seconds
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 500);
        }, 4000);
    }
});

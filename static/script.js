// Global JavaScript for Stockbit Scraper
// handles token status updates, notifications

let tokenStatusInterval;

// start token status updates on page load
document.addEventListener('DOMContentLoaded', () => {
    startTokenStatusUpdates();
});

function startTokenStatusUpdates() {
    updateTokenStatus();
    // check every 30 seconds
    tokenStatusInterval = setInterval(updateTokenStatus, 30000);
}

function updateTokenStatus() {
    fetch('/api/token/status')
        .then(r => r.json())
        .then(data => {
            const indicator = document.getElementById('token-indicator');
            const statusText = document.getElementById('token-status-text');
            const statusIcon = document.getElementById('token-status-icon');
            
            if (!indicator || !statusText || !statusIcon) return;
            
            if (data.valid) {
                const timeLeft = Math.floor(data.time_until_expiry / 60);
                statusText.textContent = `Token: ${timeLeft}m`;
                statusIcon.style.color = '#28a745'; // green
                indicator.title = 'Token is valid. Click to manage.';
            } else if (data.expired) {
                statusText.textContent = 'Token Expired';
                statusIcon.style.color = '#dc3545'; // red
                indicator.title = 'Token expired! Click to set a new one.';
            } else {
                statusText.textContent = 'No Token';
                statusIcon.style.color = '#ffc107'; // yellow
                indicator.title = 'No token set. Click to set one.';
            }
        })
        .catch(err => {
            console.error('Failed to update token status:', err);
        });
}

// Notifications
function showNotification(message, type = 'success') {
    const area = document.getElementById('notification-area');
    
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.textContent = message;
    
    area.appendChild(notification);
    
    // auto-remove after 4 seconds
    setTimeout(() => {
        notification.style.opacity = '0';
        setTimeout(() => notification.remove(), 300);
    }, 4000);
}

// Modal close on outside click
window.onclick = function(event) {
    const tokenModal = document.getElementById('token-modal');
    if (event.target === tokenModal) {
        closeTokenModal();
    }
}

// NO AUTH NEEDED! Token status updates disabled
// document.addEventListener('DOMContentLoaded', function() {
//     startTokenStatusUpdates();
// });

// window.addEventListener('beforeunload', () => {
//     if (tokenStatusInterval) {
//         clearInterval(tokenStatusInterval);
//     }
// });


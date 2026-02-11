"""
Simple token management for Stockbit - Manual Input Only
"""
import json
import base64
import requests
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from config import CONFIG_DIR, STOCKBIT_API_BASE, HEADERS_TEMPLATE

logger = logging.getLogger(__name__)

TOKEN_FILE = CONFIG_DIR / 'token.json'
WEBSOCKET_KEY_URL = f'{STOCKBIT_API_BASE}/auth/websocket/key'

class TokenManager:
    """Manages Bearer token and cookies - manual input only"""
    
    def __init__(self):
        self.token: Optional[str] = None
        self.exp: Optional[int] = None
        self.issued_at: Optional[datetime] = None
        self.cookies: Optional[str] = None  # Store cookies for WebSocket
        self._load_token()
    
    def _load_token(self):
        """Load saved token and cookies from file"""
        if TOKEN_FILE.exists():
            try:
                with open(TOKEN_FILE, 'r') as f:
                    data = json.load(f)
                    self.token = data.get('token')
                    self.exp = data.get('exp')
                    self.cookies = data.get('cookies')  # Load cookies
                    if data.get('issued_at'):
                        self.issued_at = datetime.fromisoformat(data['issued_at'])
            except Exception as e:
                print(f"Failed to load token: {e}")
    
    def _save_token(self):
        """Save token and cookies to file"""
        try:
            data = {
                'token': self.token,
                'exp': self.exp,
                'cookies': self.cookies,  # Save cookies
                'issued_at': self.issued_at.isoformat() if self.issued_at else None
            }
            with open(TOKEN_FILE, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            print(f"Failed to save token: {e}")
    
    def decode_token(self, token: str) -> Dict[str, Any]:
        """Decode JWT token to get payload"""
        try:
            # JWT format: header.payload.signature
            parts = token.split('.')
            if len(parts) != 3:
                raise ValueError("Invalid JWT format")
            
            # decode payload (add padding if needed)
            payload = parts[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += '=' * padding
            
            decoded = base64.urlsafe_b64decode(payload)
            return json.loads(decoded)
        except Exception as e:
            raise Exception(f"Failed to decode token: {e}")
    
    def get_user_id(self) -> Optional[int]:
        """Extract userId from JWT token payload"""
        if not self.token:
            return None
        
        try:
            payload = self.decode_token(self.token)
            # uid is nested in data object
            return payload.get('data', {}).get('uid')
        except Exception as e:
            logger.error(f"Failed to get userId from token: {e}")
            return None
    
    def fetch_trading_key(self, token: str = None) -> Optional[str]:
        """Fetch trading key for WebSocket connection"""
        if not token:
            token = self.get_valid_token()
        
        if not token:
            logger.error("No valid token available to fetch trading key")
            return None
        
        try:
            headers = HEADERS_TEMPLATE.copy()
            headers['Authorization'] = f'Bearer {token}'
            
            response = requests.get(
                WEBSOCKET_KEY_URL,
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 401:
                logger.error("Token expired while fetching trading key")
                self.mark_token_invalid()
                return None
            
            response.raise_for_status()
            data = response.json()
            
            trading_key = data.get('data', {}).get('key')
            if trading_key:
                logger.info("Successfully fetched trading key")
                return trading_key
            else:
                logger.error("Trading key not found in response")
                return None
                
        except Exception as e:
            logger.error(f"Failed to fetch trading key: {e}")
            return None
    
    def set_token(self, token: str, cookies: str = None) -> Dict[str, Any]:
        """Set a new token and optionally cookies manually"""
        try:
            # decode and validate
            payload = self.decode_token(token)
            
            self.token = token
            self.exp = payload.get('exp')
            self.issued_at = datetime.now()
            
            # Set cookies if provided
            if cookies:
                self.cookies = cookies
                logger.info("Cookies saved for WebSocket authentication")
            
            # save to disk
            self._save_token()
            
            return {
                'success': True,
                'message': 'Token set successfully' + (' (with cookies)' if cookies else ''),
                'expires_at': datetime.fromtimestamp(self.exp).isoformat() if self.exp else None
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'Invalid token: {str(e)}'
            }
    
    def get_cookies(self) -> Optional[str]:
        """Get stored cookies for WebSocket"""
        return self.cookies
    
    def get_valid_token(self) -> Optional[str]:
        """Get current token if still valid, None otherwise"""
        if not self.token:
            return None
        
        if self.is_expired():
            return None
        
        return self.token
    
    def is_expired(self) -> bool:
        """Check if token is expired"""
        if not self.exp:
            return False  # can't tell, assume valid
        
        # token expired?
        current_time = int(datetime.now().timestamp())
        return current_time >= self.exp
    
    def get_time_until_expiry(self) -> Optional[int]:
        """Get seconds until token expires"""
        if not self.exp:
            return None
        
        current_time = int(datetime.now().timestamp())
        return max(0, self.exp - current_time)
    
    def mark_token_invalid(self):
        """Mark token as invalid (force user to re-enter)"""
        self.token = None
        self.exp = None
        self.issued_at = None
        self._save_token()
    
    def get_status(self) -> Dict[str, Any]:
        """Get token status info"""
        if not self.token:
            return {
                'has_token': False,
                'valid': False,
                'message': 'No token set. Please enter your Bearer token.'
            }
        
        if self.is_expired():
            return {
                'has_token': True,
                'valid': False,
                'expired': True,
                'message': 'Token expired. Please enter a new token.'
            }
        
        time_left = self.get_time_until_expiry()
        return {
            'has_token': True,
            'valid': True,
            'expired': False,
            'time_until_expiry': time_left,
            'expires_at': datetime.fromtimestamp(self.exp).isoformat() if self.exp else None,
            'message': f'Token valid for {time_left // 60} minutes' if time_left else 'Token is valid',
            'token': self.token,
            'cookies': self.cookies
        }

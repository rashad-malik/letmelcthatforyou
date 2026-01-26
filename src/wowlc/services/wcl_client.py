"""
WarcraftLogs API v2 Client for WoW Loot Council.

This module handles OAuth2 authentication and GraphQL queries to the
WarcraftLogs Classic API. It supports both client credentials flow
(for public data) and user tokens (for private/archived reports).

Usage:
    # Basic usage (auto-authenticates with client credentials)
    client = WarcraftLogsClient()
    result = client.query(graphql_query, variables)

    # For private/archived reports
    client.set_user_token("user_access_token_here")
    result = client.query(graphql_query, variables)
"""

import logging
import time
from typing import Any, Optional

import requests

from wowlc.core.config import get_config_manager

# Configure module logger
logger = logging.getLogger(__name__)


class WCLAuthenticationError(Exception):
    """Raised when OAuth authentication fails."""
    pass


class WCLQueryError(Exception):
    """Raised when a GraphQL query returns errors."""
    pass


class WarcraftLogsClient:
    """
    Client for WarcraftLogs API v2 (GraphQL).
    
    Handles OAuth2 authentication (client credentials and user tokens)
    and executes GraphQL queries against the WarcraftLogs Classic API.
    
    Attributes:
        TOKEN_URL: OAuth token endpoint for client credentials flow.
        CLIENT_API_URL: GraphQL endpoint for client credentials access.
        USER_API_URL: GraphQL endpoint for user token access.
    
    Methods:
        authenticate() -> bool
            Get access token using client credentials. Called automatically on first query.
        
        set_user_token(token: str) -> None
            Set a user access token for accessing private/archived reports.
            Switches the client to use the user API endpoint.
        
        query(graphql_query: str, variables: dict = None) -> dict
            Execute a GraphQL query. Auto-refreshes token if expired.
            Returns the "data" portion of the response.
            Raises WCLQueryError on GraphQL errors.
        
        is_authenticated() -> bool
            Check if client has a valid (non-expired) token.
    """
    
    # WarcraftLogs Classic API endpoints
    TOKEN_URL: str = "https://classic.warcraftlogs.com/oauth/token"
    CLIENT_API_URL: str = "https://classic.warcraftlogs.com/api/v2/client"
    USER_API_URL: str = "https://classic.warcraftlogs.com/api/v2/user"
    
    # Token expiry buffer in seconds
    TOKEN_EXPIRY_BUFFER: int = 60
    
    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        user_token: Optional[str] = None,
    ) -> None:
        """
        Initialize the WarcraftLogs client.

        Args:
            client_id: OAuth client ID. Defaults to config value.
            client_secret: OAuth client secret. Defaults to config value.
            user_token: Optional pre-obtained user access token. Defaults to config value.
        """
        config = get_config_manager()
        self._client_id: Optional[str] = client_id or config.get_wcl_client_id()
        self._client_secret: Optional[str] = client_secret or config.get_wcl_client_secret()

        # Token state
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._using_user_token: bool = False

        # Check for pre-configured user token
        env_user_token = user_token or config.get_wcl_user_token()
        if env_user_token:
            self.set_user_token(env_user_token)
    
    def _has_client_credentials(self) -> bool:
        """Check if client credentials are available."""
        return bool(self._client_id and self._client_secret)
    
    def authenticate(self) -> bool:
        """
        Authenticate using client credentials flow.
        
        Obtains an access token from the WarcraftLogs OAuth endpoint using
        the configured client_id and client_secret.
        
        Returns:
            True if authentication was successful.
        
        Raises:
            WCLAuthenticationError: If credentials are missing or authentication fails.
        """
        if not self._has_client_credentials():
            raise WCLAuthenticationError(
                "Missing client credentials. Set WCL_CLIENT_ID and WCL_CLIENT_SECRET "
                "environment variables or pass them to the constructor."
            )
        
        logger.info("Authenticating with WarcraftLogs API...")
        
        try:
            response = requests.post(
                self.TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                },
                auth=(self._client_id, self._client_secret),
                timeout=30,
            )
            response.raise_for_status()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Authentication request failed: {e}")
            raise WCLAuthenticationError(f"Failed to connect to WarcraftLogs OAuth endpoint: {e}")
        
        try:
            token_data = response.json()
        except ValueError as e:
            logger.error(f"Failed to parse authentication response: {e}")
            raise WCLAuthenticationError("Invalid response from WarcraftLogs OAuth endpoint")
        
        if "access_token" not in token_data:
            error_msg = token_data.get("error_description", token_data.get("error", "Unknown error"))
            logger.error(f"Authentication failed: {error_msg}")
            raise WCLAuthenticationError(f"Authentication failed: {error_msg}")
        
        self._access_token = token_data["access_token"]
        expires_in = token_data.get("expires_in", 3600)
        self._token_expires_at = time.time() + expires_in - self.TOKEN_EXPIRY_BUFFER
        self._using_user_token = False
        
        logger.info(f"Successfully authenticated. Token expires in {expires_in} seconds.")
        return True
    
    def set_user_token(self, token: str) -> None:
        """
        Set a user access token for accessing private/archived reports.
        
        When a user token is set, the client switches to use the user API
        endpoint and disables automatic token refresh (user tokens are
        managed externally through the browser OAuth flow).
        
        Args:
            token: The user access token obtained from the OAuth flow.
        """
        if not token:
            raise ValueError("User token cannot be empty")
        
        self._access_token = token
        self._using_user_token = True
        # User tokens don't have automatic expiry tracking - they're managed externally
        self._token_expires_at = float("inf")
        
        logger.info("User token set. Using user API endpoint for queries.")
    
    def clear_user_token(self) -> None:
        """
        Clear the user token and revert to client credentials mode.
        
        After calling this, the next query will trigger re-authentication
        using client credentials.
        """
        self._access_token = None
        self._token_expires_at = 0.0
        self._using_user_token = False
        
        logger.info("User token cleared. Reverted to client credentials mode.")
    
    def is_authenticated(self) -> bool:
        """
        Check if the client has a valid (non-expired) token.
        
        Returns:
            True if a valid token exists and hasn't expired.
        """
        if not self._access_token:
            return False
        
        if self._using_user_token:
            # User tokens are always considered valid (managed externally)
            return True
        
        return time.time() < self._token_expires_at
    
    def _get_api_url(self) -> str:
        """Get the appropriate API URL based on authentication mode."""
        return self.USER_API_URL if self._using_user_token else self.CLIENT_API_URL
    
    def _ensure_authenticated(self) -> None:
        """
        Ensure the client is authenticated before making a query.
        
        For client credentials mode, this will auto-refresh expired tokens.
        For user token mode, this only checks that a token is set.
        
        Raises:
            WCLAuthenticationError: If authentication fails or no token is available.
        """
        if self.is_authenticated():
            return
        
        if self._using_user_token:
            # User token mode but no token set - shouldn't happen, but check anyway
            raise WCLAuthenticationError("User token is not set or has been cleared.")
        
        # Client credentials mode - authenticate or refresh
        self.authenticate()
    
    def query(
        self,
        graphql_query: str,
        variables: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Execute a GraphQL query against the WarcraftLogs API.
        
        Automatically handles authentication and token refresh for client
        credentials mode. For user token mode, assumes the token is valid.
        
        Args:
            graphql_query: The GraphQL query string.
            variables: Optional dictionary of query variables.
        
        Returns:
            The "data" portion of the GraphQL response.
        
        Raises:
            WCLAuthenticationError: If authentication fails.
            WCLQueryError: If the query returns GraphQL errors.
        """
        self._ensure_authenticated()
        
        api_url = self._get_api_url()
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        
        payload: dict[str, Any] = {"query": graphql_query}
        if variables:
            payload["variables"] = variables
        
        try:
            response = requests.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=60,
            )
            response.raise_for_status()
            
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401:
                # Token might have been revoked or expired unexpectedly
                logger.warning("Received 401 Unauthorized. Token may have been revoked.")
                if not self._using_user_token:
                    # Try re-authenticating once for client credentials mode
                    logger.info("Attempting re-authentication...")
                    self._access_token = None
                    self._token_expires_at = 0.0
                    self.authenticate()
                    # Retry the query
                    return self.query(graphql_query, variables)
                else:
                    raise WCLAuthenticationError(
                        "User token is invalid or has expired. Please obtain a new token."
                    )
            
            logger.error(f"Query request failed with HTTP {response.status_code}: {e}")
            raise WCLQueryError(f"HTTP error during query: {response.status_code} - {e}")
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Query request failed: {e}")
            raise WCLQueryError(f"Network error during query: {e}")
        
        try:
            result = response.json()
        except ValueError as e:
            logger.error(f"Failed to parse query response: {e}")
            raise WCLQueryError("Invalid JSON response from WarcraftLogs API")
        
        # Check for GraphQL errors
        if "errors" in result and result["errors"]:
            error_messages = [
                err.get("message", "Unknown error") for err in result["errors"]
            ]
            error_str = "; ".join(error_messages)
            logger.error(f"GraphQL query returned errors: {error_str}")
            raise WCLQueryError(f"GraphQL errors: {error_str}")
        
        # Return the data portion
        return result.get("data", {})
    
    def get_token_info(self) -> dict[str, Any]:
        """
        Get information about the current authentication state.
        
        Returns:
            Dictionary with token state information.
        """
        if not self._access_token:
            return {
                "authenticated": False,
                "mode": None,
                "expires_in": None,
            }
        
        if self._using_user_token:
            return {
                "authenticated": True,
                "mode": "user_token",
                "expires_in": None,  # User tokens don't have tracked expiry
            }
        
        expires_in = max(0, int(self._token_expires_at - time.time()))
        return {
            "authenticated": True,
            "mode": "client_credentials",
            "expires_in": expires_in,
        }
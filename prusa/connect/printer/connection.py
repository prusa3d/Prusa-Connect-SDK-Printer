"""Module repsponsible for connection with Connect."""
from requests import post, get


class Connection:
    """Connection class for printer."""
    def __init__(self, server: str, fingerprint: str, token: str = None):
        self.server = server
        self.fingerprint = fingerprint
        self.token = token

    def make_headers(self, timestamp: float) -> dict:
        """Return request headers from connection variables."""
        headers = {
            "Fingerprint": self.fingerprint,
            "Timestamp": str(timestamp)
        }
        if self.token:
            headers['Token'] = self.token
        return headers

    def post(self, url: str, headers: dict, data: dict):
        """Call HTTP POST on connection."""
        return post(self.server + url, headers=headers, json=data)

    def get(self, url: str, headers: dict):
        """Call HTTP GET on connection."""
        return get(self.server + url, url, headers=headers)

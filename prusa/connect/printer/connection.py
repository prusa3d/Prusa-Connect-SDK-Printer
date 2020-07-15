from requests import post, get


class Connection:
    """Connection class for printer."""

    def __init__(self, server: str, fingerprint: str, token: str = None):
        self.server = server
        self.fingerprint = fingerprint
        self.token = token

    def make_headers(self, timestamp: int) -> dict:
        """Return request headers from connection variables."""
        headers = {
            "Printer-Fingerprint": self.fingerprint,
            "Timestamp": str(timestamp)
        }
        if self.token:
            headers['Printer-Token'] = self.token
        return headers

    def post(self, url: str, headers: dict, data: dict):
        return post(self.server + url, headers=headers, json=data)

    def get(self, url: str, headers: dict):
        return get(self.server + url, url, headers=headers)
